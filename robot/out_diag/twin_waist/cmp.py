import numpy as np,json,sys
sys.path.insert(0,'twin_v4'); from metrics_calc import calc
R={}
for m,p in [('baseline_v4','twin_v4'),('slew','twin_waist/slew'),('lowpass','twin_waist/lowpass'),('off','twin_waist/off')]:
    o,r=calc(p); z=np.load(p+'/trace.npz'); bp=z['bp']; t=bp[:,0]; dt=np.median(np.diff(t)); u=bp[:,1:5]
    v=np.diff(u,axis=0)/dt; j=np.diff(u,2,axis=0)/dt**2
    S=z['states']
    R[m]=dict(phases=o,falls=sum(x['fall_ticks'] for x in o.values()),unsafe=r.get('unsafe'),
      cmd_rms_jerk=[float(np.sqrt((j[:,i]**2).mean())) for i in range(4)],cmd_vel_max=[float(np.abs(v[:,i]).max()) for i in range(4)],
      cmd_range=[float(np.ptp(u[:,i])) for i in range(4)],tilt_max=float(S[:,2].max()),pelvis_min=float(S[:,1].min()),
      tilt_rms=float(np.sqrt((S[:,2]**2).mean())),clip_pct={k:v['clipped_pct'] for k,v in o.items()})
json.dump(R,open('twin_waist/metrics.json','w'),indent=1)
L=['# Waist modes on twin\n','NOTE: trace.npz holds only waist COMMAND (bp), arms, pelvis/tilt; no measured waist/leg joints, so PSD>3Hz of measured waist velocity and leg vel RMS are NOT computable. Command jerk used instead.\n','| metric | '+' | '.join(R)+' |','|'+'---|'*(len(R)+1)]
def row(n,f): L.append('| '+n+' | '+' | '.join(f(R[m]) for m in R)+' |')
row('fall ticks',lambda r:str(r['falls']));row('unsafe',lambda r:str(r['unsafe']))
row('tilt max deg',lambda r:'%.2f'%r['tilt_max']);row('tilt rms',lambda r:'%.2f'%r['tilt_rms']);row('pelvis min',lambda r:'%.3f'%r['pelvis_min'])
row('waist cmd jerk rms /axis',lambda r:','.join('%.3g'%x for x in r['cmd_rms_jerk']))
row('waist cmd vel max /axis',lambda r:','.join('%.3g'%x for x in r['cmd_vel_max']))
row('waist cmd range /axis',lambda r:','.join('%.3g'%x for x in r['cmd_range']))
for ph in R['off']['phases']: row('arm clip% '+ph,lambda r,ph=ph:str(r['clip_pct'].get(ph)))
open('twin_waist/COMPARE.md','w').write('\n'.join(L)+'\n'); print('\n'.join(L))
