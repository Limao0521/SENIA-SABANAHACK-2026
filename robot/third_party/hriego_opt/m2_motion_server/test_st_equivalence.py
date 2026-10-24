import sys, time, json, numpy as np, random
from m2common import *
import st_stream as ST
ST.load()
import torch
clips=json.load(open(CLIPS_JSON)); import librosa
a=librosa.load(clips["c010"], sr=16000)[0]
r,tm=ST.W.gen(a,1)
blocks=[p for n,nw,p,tr,i in ST.stream(a,1)]
o=np.concatenate(blocks); d=np.abs(o-r["poses"]).max(1)
print([b.shape[0] for b in blocks]); print(d.round(2)[:60])
