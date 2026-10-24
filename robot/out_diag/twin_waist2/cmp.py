import numpy as np,json,sys
sys.path.insert(0,'twin_v4'); from metrics_calc import calc
from scipy.signal import welch
R={}
for m,p in [('off','twin_waist2/off'),('lowpass','twin_waist2/lowpass'),('old_off','twin_waist/off'),('old_lowpass','twin_waist/lowpass')]:
    o,r=calc(p); z=np.load(p+'/trace.npz'); bp=z['bp']; S=z['states']
    u=bp[:,1:4]; nz=float((np.abs(u).max(1)>1e-6).mean())
    sf=[float((np.diff(np.sign(u[np.abs(u[:,i])>1e-6,i]))!=0).sum()/len(u)) for i in range(3)]
    d=dict(falls=sum(x['fall_ticks'] for x in o.values()),tilt_max=float(S[:,2].max()),tilt_rms=float(np.sqrt((S[:,2]**2).mean())),
      pelvis_min=float(S[:,1].min()),clip_pct={k:v['clipped_pct'] for k,v in o.items()},cmd_nonzero_frac=nz,cmd_signflips_per_tick=sf)
    if 'meas_waist' in z.files and len(z['meas_waist']):
        w=z['meas_waist']; t=w[:,0]; dt=np.median(np.diff(t)); v=np.diff(w[:,1:4],axis=0)/dt
        d['meas_vel_max']=[float(np.abs(v[:,i]).max()) for i in range(3)]
        hf=[]
        for i in range(3):
            f,P=welch(v[:,i]-v[:,i].mean(),fs=1/dt,nperseg=min(256,len(v))); hf.append(float(P[f>3].sum()/P.sum()))
        d['meas_vel_psd_gt3hz']=hf
    d['meas_legs']= int(len(z['meas_legs'])) if 'meas_legs' in z.files else 0
    R[m]=d
json.dump(R,open('twin_waist2/metrics.json','w'),indent=1)
f=lambda x:'['+','.join('%.3g'%a for a in x)+']' if isinstance(x,list) else str(x)
L=['# Waist off vs lowpass (twin)\n','| metric | '+' | '.join(R)+' |','|'+'---|'*(len(R)+1)]
for k in ['falls','tilt_max','tilt_rms','pelvis_min','cmd_nonzero_frac','cmd_signflips_per_tick','meas_vel_max','meas_vel_psd_gt3hz','meas_legs']:
    L.append('| '+k+' | '+' | '.join(f(R[m].get(k,'n/a')) for m in R)+' |')
for ph in R['off']['clip_pct']: L.append('| armclip% '+ph+' | '+' | '.join(str(R[m]['clip_pct'].get(ph,'n/a')) for m in R)+' |')
L.append('\nold_* = previous twin_waist runs (no measured data). meas_legs empty (0 rows) -> leg vel RMS not computable.')
open('twin_waist2/COMPARE.md','w').write('\n'.join(L)+'\n');print('\n'.join(L))
