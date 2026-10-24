import numpy as np
import torch

from x2_ultra.control import obs_spec as S
from x2_ultra.rl.env import EnvCfg, X2ArmsFreeEnv


def test_env_steps_and_shapes():
    env = X2ArmsFreeEnv(EnvCfg(num_envs=32, nthread=4, device="cpu", seed=0))
    obs = env.get_observations()
    assert obs["policy"].shape == (32, S.OBS_DIM)
    for _ in range(30):
        obs, rew, done, extras = env.step(torch.zeros(32, S.NUM_ACTIONS))
        assert torch.isfinite(obs["policy"]).all() and torch.isfinite(rew).all()
        assert "time_outs" in extras
    # rsl_rl desincroniza los episodios escribiendo episode_length_buf
    env.episode_length_buf = torch.full((32,), 500)
    assert int(env.ep_steps[0]) == 500
    env.close()
