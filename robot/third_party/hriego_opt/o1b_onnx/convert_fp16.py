"""fp32 ONNX -> fp16 ONNX (onnxruntime.transformers.float16, keep fp32 I/O). variants: 'fp16' (default block list), 'fp16ln' (LayerNorm/Softmax/ReduceMean kept fp32 = autocast-like)."""
import sys, onnx
from onnxruntime.transformers.float16 import convert_float_to_float16
from common_paths import MODELS
name, var = sys.argv[1], sys.argv[2]
m = onnx.load(str(MODELS / f"{name}_fp32.onnx"))
ops = sorted({n.op_type for n in m.graph.node}); print(name, "ops:", ops)
block = ["LayerNormalization", "Softmax", "ReduceMean", "SoftmaxCrossEntropyLoss"] if var == "fp16ln" else []
m16 = convert_float_to_float16(m, keep_io_types=True, op_block_list=None if not block else None, disable_shape_infer=False) if not block else \
      convert_float_to_float16(m, keep_io_types=True, op_block_list=block + ["Range", "Resize", "Min", "Max"], disable_shape_infer=False)
seen = set(); keep = []
for n in m16.graph.node:
    key = (n.name, tuple(n.output))
    if n.name in seen: continue   # converter emits the same weight-cast node once per blocked consumer: identical duplicates
    seen.add(n.name); keep.append(n)
if len(keep) != len(m16.graph.node):
    print("dropped duplicate nodes:", len(m16.graph.node) - len(keep)); del m16.graph.node[:]; m16.graph.node.extend(keep)
out = MODELS / f"{name}_{var}.onnx"
onnx.save_model(m16, str(out), save_as_external_data=True, all_tensors_to_one_file=True, location=out.name + ".data")
print("saved", out)
