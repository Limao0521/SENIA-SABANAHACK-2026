# python Test_repaint.py /home/mm/Downloads/epoch_999 /media/mm/ssd3T/data/audio/tongyi/xinchunjiajie.wav --data_id=3  --cfg=1.1 --L0=5
import time
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))  # HRIEGO_gesture root -> paths.py
import paths as _hp  # noqa: E402
from pathlib import Path
import torch
import argparse
import librosa
import torch.nn as nn

from transformers import Wav2Vec2Processor, HubertModel, AutoModelForCTC,WavLMModel
from Common.Util import *
from einops import rearrange
import random
from Common.Util import  NormalizeRot6d, rotation_6d_to_matrix, rotation_6d_to_axis_angle, SplitIntoOverlapedWindows, rotation_matrix_to_axis_angle, axis_angle_to_rotation_6d, matrix_to_rotation_6d
# from Net import  DiffusionDITNetParts as MotionModel
from Net import DiffusionDITNetPartsFixedExpressions2PostNormInteraction2 as MotionModel
from Common.SMPLX_Util import GetSMPLXModel, SplitPosesIntoSmplxParams, global_to_local_batch, LocalToGlobalAA, GetSMPLXModel, GlobalToLocalAA
from Common.SMPLXConfig import GetBodyPartIndices
import pandas as pd
import os
from tqdm import tqdm
from Net import SimpleSpeechModel
import torch.nn.functional as F
import numpy as np
import random
import time
from scipy.io import loadmat

from FindNearest import MakeKDTree

BUNDLED_FACE_CHECKPOINT = (
    _hp.streamtalk_root() / "Scripts/FM/ckpt/split/SimpleSpeechModel/best.pt"
)


def setup_seed(seed=0):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    random.seed(seed)
    # torch.backends.cudnn.deterministic = True

def WriteObj(filename, verts, faces=None):
    with open(filename, 'w') as fh:
        for v in verts:
            fh.write('v %f %f %f\n' % (v[0], v[1], v[2]))
        if faces is not None:
            for f in faces:
                fh.write('f %d %d %d\n' % (f[0] + 1, f[1] + 1, f[2] + 1))


class AudioFeatureExtractor(nn.Module):
    def __init__(self, model_id):
        super(AudioFeatureExtractor, self).__init__()
        self.processor = Wav2Vec2Processor.from_pretrained(model_id)
        self.model = WavLMModel.from_pretrained(model_id)

    def extract(self, audio, overlap_frame_size=8):
        if isinstance(audio, str):
            raw_audio, sr = librosa.load(audio, sr=16000)
        else:
            raw_audio = audio
            sr = 16000
        win_size = sr * 2
        stride_step = win_size - int(sr / 30 * overlap_frame_size)
        overlap_size = win_size - stride_step
        full_starts = list(range(0, len(raw_audio) - win_size + 1, stride_step))
        if full_starts:
            raw_audio_clips = [raw_audio[s:s + win_size] for s in full_starts]
            self.output_frame_counts = [60] + [60 - overlap_frame_size] * (len(full_starts) - 1)
            last_full_end = full_starts[-1] + win_size
            if len(raw_audio) > last_full_end:
                partial_start = full_starts[-1] + stride_step
                partial = raw_audio[partial_start:partial_start + win_size]
                raw_audio_clips.append(partial)
                new_audio_samples = max(0, len(raw_audio) - last_full_end)
                new_frames = int(np.ceil(new_audio_samples / stride_step * (60 - overlap_frame_size)))
                self.output_frame_counts.append(min(60 - overlap_frame_size, new_frames))
        else:
            raw_audio_clips = [raw_audio]
            valid_frames = int(np.ceil(len(raw_audio) / win_size * 60))
            self.output_frame_counts = [max(1, min(60, valid_frames))]

        huberts = []
        for aud_idx, aud_clip in enumerate(raw_audio_clips):
            if len(aud_clip) < win_size:
                aud_clip = np.pad(aud_clip, (0, win_size - len(aud_clip)))
            input_values = self.processor(aud_clip, return_tensors='pt', sampling_rate=16000).input_values.cuda()
            with torch.no_grad(), torch.autocast(device_type='cuda', dtype=torch.float16):
                audio_embeding = self.model.forward(input_values).last_hidden_state
                audio_embeding = F.interpolate(audio_embeding.permute((0, 2, 1)), 60, mode='linear', align_corners=True).permute((0, 2, 1))
            huberts.append(audio_embeding)
        print(f"audio windows={len(huberts)} output_frame_counts={self.output_frame_counts}")
        return huberts


class EvaluateSMPLX:
    def __init__(self, model_path):
        self.smplx_model = GetSMPLXModel(model_path).requires_grad_(False).cuda().eval()

    def __call__(self, betas, poses):
        params = SplitPosesIntoSmplxParams(poses)
        params['global_orient'] = torch.zeros_like(params['global_orient'])
        with torch.no_grad():
            output = self.smplx_model(betas=betas.expand(len(poses), -1),
                                    **params,
                                    transl=torch.zeros((len(poses), 3), device=poses.device),
                                    expression=torch.zeros((len(poses), 100), device=poses.device) )
            joints = output['joints'][:,:55]
        return joints


def linear_blending_refinement(xt, idx):
    B, L, C = xt.shape
    res  =xt.clone()
    start_idx = idx - 8

    eps = torch.linspace(0,1, 9).reshape((1,9,1)).repeat(B, 1,1).to(xt.device)

    res[:,start_idx:idx + 1] = res[:,[start_idx]] * (1 - eps) + res[:,[idx]] *eps

    end_idx = min(idx + 8, L-1)
    if end_idx - idx >=3:
        eps = torch.linspace(0,1, end_idx - idx + 1).reshape((1, end_idx - idx + 1, 1)).repeat(B,1,1).to(xt.device)
        res[:,idx:end_idx + 1] = res[:,[idx]] * (1 - eps) + res[:,[L-1]] * eps
    return res




    
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=str)
    parser.add_argument("ckpt", type=str)
    parser.add_argument(
        "--example_motion_npz",
        type=str,
        default=str(
            _hp.READY_PROMPT
        ),
    )
    parser.add_argument("--pid", type=str, default="2_scott_0")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--cfg_scale",
        type=float,
        default=1.0,
        help="classifier-free guidance scale; released retrained checkpoints use 3",
    )
    parser.add_argument("--local", type=int, default="0")
    parser.add_argument("--output_file", type=str, default="test.npz")
    parser.add_argument(
        "--wavlm_model",
        type=str,
        required=True,
        help="Hugging Face model id or local WavLM directory",
    )
    parser.add_argument("--smplx_model", type=str, required=True)
    parser.add_argument("--retrieval_data_folder", type=str, required=True)
    parser.add_argument("--retrieval_csv_file", type=str, required=True)
    parser.add_argument("--retrieval_mode", choices=("native", "no_anchor"), default="native")
    parser.add_argument("--keypose_npz", type=str, default=None,
                        help="optional externally planned pose hint; not a semantic intent token")
    parser.add_argument("--keypose_frame", type=int, default=0)
    parser.add_argument("--keypose_start", type=int, default=None,
                        help="first prompt slot for an external pose hint; allowed range [52,59]")
    parser.add_argument("--keypose_end", type=int, default=None,
                        help="last prompt slot for an external pose hint; allowed range [52,59]")

    args = parser.parse_args()

    # Keep the single GPU inference cooperative with other research processes.
    torch.set_num_threads(2)
    try:
        torch.set_num_interop_threads(2)
    except RuntimeError:
        pass

    if not 0.0 <= args.cfg_scale <= 7.0:
        parser.error("--cfg_scale must be between 0 and 7")

    setup_seed(args.seed)
    
    hand_indices = GetBodyPartIndices("hand", "rot6d")
    upper_indices = GetBodyPartIndices("upper", "rot6d")
    lower_indices = GetBodyPartIndices("lower", "rot6d")
    face_joint_indices = GetBodyPartIndices("face", "rot6d")
    trans_indices = np.array([330, 331, 332]) 

    n_seed = 8
    has_trans = True

    if not BUNDLED_FACE_CHECKPOINT.is_file():
        raise FileNotFoundError(
            f"repository asset is missing: {BUNDLED_FACE_CHECKPOINT}"
        )
    simple_speech_model = SimpleSpeechModel().cuda().requires_grad_(False).eval()
    simple_speech_model.load_state_dict(
        torch.load(BUNDLED_FACE_CHECKPOINT, map_location="cpu")
    )


    net = MotionModel().cuda().requires_grad_(False).eval()
    net.load_state_dict(torch.load(args.ckpt, map_location="cpu"), strict=True)

    # net = torch.compile(net)

    smplx_model = EvaluateSMPLX(args.smplx_model)

    kdtree_info = None
    if args.retrieval_mode == "native":
        kdtree_info = MakeKDTree(
            pid=args.pid,
            data_folder=args.retrieval_data_folder,
            csv_file=args.retrieval_csv_file,
            smplx_model_path=args.smplx_model,
        )
        kdtree_info['poses'] = torch.from_numpy(LocalToGlobalAA(kdtree_info['poses'])).float()
        kdtree_info['poses'] = axis_angle_to_rotation_6d(kdtree_info['poses']).cuda()
        kdtree_info['joints'] = torch.from_numpy(kdtree_info['joints']).float().cuda().half()



    pid = torch.LongTensor([int(args.pid.split('_')[0])]).cuda()

    exampled_pose_file = args.example_motion_npz
    smplx_data = np.load(exampled_pose_file, allow_pickle=True)
    betas = smplx_data["betas"].astype(np.float32)
    poses = smplx_data["poses"].astype(np.float32)
    trans = smplx_data["trans"].astype(np.float32)


    pose_prompt = axis_angle_to_rotation_6d(LocalToGlobalAA(poses[:60]))
    pose_prompt = torch.from_numpy(pose_prompt).cuda().unsqueeze(0).float()
    trans_prompt = torch.from_numpy(trans[:60]).cuda().unsqueeze(0).float()
    explicit_keypose = None
    if args.keypose_npz:
        keypose_start = 59 if args.keypose_start is None else args.keypose_start
        keypose_end = keypose_start if args.keypose_end is None else args.keypose_end
        if not 52 <= keypose_start <= keypose_end <= 59:
            parser.error("keypose slot range must be ordered and inside released late-keypose slots [52,59]")
        keypose_data = np.load(args.keypose_npz, allow_pickle=True)
        keypose_poses = np.asarray(keypose_data["poses"], dtype=np.float32)
        if not 0 <= args.keypose_frame < len(keypose_poses):
            parser.error(f"--keypose_frame must be in [0,{len(keypose_poses)-1}]")
        keypose_global = LocalToGlobalAA(keypose_poses[args.keypose_frame:args.keypose_frame + 1])
        explicit_keypose = torch.from_numpy(axis_angle_to_rotation_6d(keypose_global)).cuda().float().reshape(1, 55 * 6)
        print(f"[explicit-keypose] loaded motor pose hint {args.keypose_npz}; injection slots={keypose_start}:{keypose_end}; last audio chunk only", flush=True)

    audio_npy = librosa.load(args.audio, sr=16000)[0] 
    audio_feature_extractor = AudioFeatureExtractor(
        args.wavlm_model
    ).requires_grad_(False).cuda().eval()

    time_start = time.time()
    huberts_start_time = time.time()
    huberts = audio_feature_extractor.extract(audio_npy)
    torch.cuda.synchronize()
    huberts_end_time = time.time()
    num_segments = len(huberts)
    print('Hubert time per inference chunk: ', (huberts_end_time - huberts_start_time) / num_segments)

    B = 1
    L = 60
    motions = []

    intial_sample_avg_time = 0.0
    spg_retrieval_avg_time = 0.0
    fk_avg_time = 0.0
    for n in  tqdm(range(num_segments), total=num_segments):
        prompt_mask = torch.full((B, 60), True).cuda()
        prompt_mask[:, :n_seed] = False
        pred_expressions = simple_speech_model(huberts[n], pid)
        pred_expressions_uncond = None
        if args.cfg_scale != 1.0:
            pred_expressions_uncond = simple_speech_model(
                huberts[n], torch.zeros_like(pid)
            )
        trans_mask = torch.full((B, 60), True).cuda()
        trans_mask[:, :n_seed] = False
        model_kwargs = {
                "pid": pid,
                "poses_prompt": pose_prompt,
                "prompt_mask":prompt_mask,
                "trans_prompt":trans_prompt,
                "trans_mask":trans_mask,
                "hand_indices": hand_indices,
                "upper_indices": upper_indices,
                "lower_indices": lower_indices,
                "face_joint_indices": face_joint_indices,
                "trans_indices": trans_indices,
                "hubert":huberts[n],
                "expressions":pred_expressions,
                }
        if pred_expressions_uncond is not None:
            model_kwargs["expressions_uncond"] = pred_expressions_uncond

        noise = torch.randn((B, 60, 55 * 6 + 3*has_trans)).cuda()
        initial_sample_start_time = time.time()
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16):
           xt_list = net.sample(
               noise,
               normalize_func=NormalizeRot6d,
               cfg_scale=args.cfg_scale,
               **model_kwargs,
           )
           xt = xt_list[-1]
        torch.cuda.synchronize()
        initial_sample_end_time = time.time()
        # print('Initial sample time: ', initial_sample_end_time - initial_sample_start_time)
        intial_sample_avg_time += (initial_sample_end_time - initial_sample_start_time)


        
        xt[...,:330] = NormalizeRot6d(xt[...,:330])
        local_poses = rotation_6d_to_matrix(xt[...,:330].reshape(-1, 55, 6))
        local_poses = global_to_local_batch(local_poses)
        local_poses = rotation_matrix_to_axis_angle(local_poses).reshape(B, -1, 55 * 3)

        fk_start_time = time.time()
        joints = smplx_model(betas=torch.from_numpy(betas).float().cuda(), 
                             poses=local_poses.reshape(-1, 55 * 3).cuda()).reshape((60, 1, -1))
        torch.cuda.synchronize()
        fk_end_time = time.time()
        fk_avg_time += (fk_end_time - fk_start_time)

        retrieval_start_time = time.time()
        if args.retrieval_mode == "native":
            # Preserve exact L1 retrieval with a bounded GPU temporary.
            retrieval_db = kdtree_info['joints']
            retrieval_query = joints[-8:].reshape(8, -1).half()
            best_dists = torch.full((8,), float('inf'), dtype=retrieval_query.dtype,
                                    device=retrieval_query.device)
            best_ids = torch.zeros((8,), dtype=torch.long, device=retrieval_query.device)
            chunk_size = int(os.environ.get("RCP_RETRIEVAL_CHUNK", "1024"))
            for db_start in range(0, retrieval_db.shape[0], chunk_size):
                db_end = min(db_start + chunk_size, retrieval_db.shape[0])
                chunk_distances = torch.abs(
                    retrieval_db[None, db_start:db_end, :] - retrieval_query[:, None, :]
                ).sum(dim=-1)
                chunk_best, chunk_ids = chunk_distances.min(dim=-1)
                replace = chunk_best < best_dists
                best_dists[replace] = chunk_best[replace]
                best_ids[replace] = chunk_ids[replace] + db_start
            dists, nearest_ids = best_dists, best_ids
            nearest_idx = int(torch.argmin(dists).item())
            nearest_poses = kdtree_info['poses'][nearest_ids[nearest_idx]].reshape(1, 55, 6)
            nearest_poses = rotation_6d_to_matrix(nearest_poses)
            global_orientation = rotation_6d_to_matrix(xt[0, 52 + nearest_idx, :6].reshape(1, 6))
            nearest_poses = torch.einsum('bij,bnjk->bnik', global_orientation, nearest_poses)
            nearest_poses = matrix_to_rotation_6d(nearest_poses).reshape(1, 55 * 6)
            prompt_mask = torch.full((B, 60), True).cuda()
            prompt_mask[:, :n_seed] = False
            prompt_mask[:, 52 + nearest_idx] = False
            pose_prompt[:, 52 + nearest_idx] = nearest_poses
            xt[:, 52 + nearest_idx, :330] = nearest_poses
        else:
            # Ablation: retain audio, neutral seed prompt, and released diffusion
            # continuation; omit the retrieval pose injection.
            prompt_mask = torch.full((B, 60), True).cuda()
            prompt_mask[:, :n_seed] = False
        if explicit_keypose is not None and n == num_segments - 1:
            pose_prompt[:, keypose_start:keypose_end + 1] = explicit_keypose[:, None]
            prompt_mask[:, keypose_start:keypose_end + 1] = False
        torch.cuda.synchronize()
        retrieval_end_time = time.time()
        spg_retrieval_avg_time += retrieval_end_time - retrieval_start_time

        model_kwargs = {
                "pid": pid,
                "poses_prompt": pose_prompt,
                "prompt_mask":prompt_mask,
                "trans_prompt":trans_prompt,
                "trans_mask":trans_mask,
                "hand_indices": hand_indices,
                "upper_indices": upper_indices,
                "lower_indices": lower_indices,
                "face_joint_indices": face_joint_indices,
                "trans_indices": trans_indices,
                "hubert":huberts[n],
                "expressions":pred_expressions,
                }
        if pred_expressions_uncond is not None:
            model_kwargs["expressions_uncond"] = pred_expressions_uncond

        # noise = torch.randn((B, 60, 55 * 6)).cuda()
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True):
            xt = net.sample_with_kp_bk(
                noise,
                xt_list[5],
                5,
                normalize_func=NormalizeRot6d,
                cfg_scale=args.cfg_scale,
                **model_kwargs,
            )
            # xt[...,:330] = linear_blending_refinement(xt[...,:330] , 52 + torch.argmin(dists))
            
        xt[...,:330] = NormalizeRot6d(xt[...,:330])
        pose_prompt[:,:n_seed] = xt[:,-n_seed:][..., :330]
        if has_trans:
            trans_prompt[:,:n_seed] = xt[:,-n_seed:][..., 330:333]

        xt = torch.cat([xt, pred_expressions], dim=-1)
        motions.append(xt)
    for i in range(1, len(motions)):
        motions[i-1][:,-n_seed:] = (motions[i-1][:,-n_seed:] + motions[i][:,:n_seed])/2.0
        motions[i] = motions[i][:,n_seed:n_seed + audio_feature_extractor.output_frame_counts[i]]
    if len(motions) == 1:
        motions[0] = motions[0][:, :audio_feature_extractor.output_frame_counts[0]]
    
    sample_rot6d = torch.cat(motions, dim=1)
    pred_expressions = sample_rot6d[...,-100:]

    print("intial_sample_avg_time", intial_sample_avg_time / num_segments)
    print("spg_retrieval_avg_time", spg_retrieval_avg_time / num_segments)
    print("fk_avg_time", fk_avg_time / num_segments)

    if args.local == 0:
        pred_poses = rotation_6d_to_matrix(sample_rot6d[...,:330].reshape(-1, 55, 6))
        pred_poses = global_to_local_batch(pred_poses)
        pred_poses = rotation_matrix_to_axis_angle(pred_poses).reshape(B, -1, 55 * 3)
    else:
        pred_poses = sample_rot6d[...,:330].reshape(B, -1, 55 * 6)
        pred_poses = rotation_6d_to_axis_angle(pred_poses)
    if has_trans:
        pred_trans = sample_rot6d[...,330:330 + 3:]
    else:
        pred_trans = torch.zeros((B, sample_rot6d.shape[1], 3)).cuda()

    time_end = time.time()
    print('#pred_poses', pred_poses.shape)
    print('estimated fps', pred_poses.shape[1]/ (time_end - time_start))



    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output_path,
        betas=betas,
        poses=pred_poses[0].cpu().numpy(),
        expressions=pred_expressions[0].cpu().numpy(),
        trans= pred_trans[0].cpu().numpy(),
        gender=np.array("neutral"),
        mocap_frame_rate=np.array(30),
        model="smplx2020",
    )
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        print("[gpu_peak] allocated_mib=%.1f reserved_mib=%.1f" % (
            torch.cuda.max_memory_allocated() / 2**20,
            torch.cuda.max_memory_reserved() / 2**20,
        ), flush=True)

    


if __name__ == '__main__':
    main()
