import numpy as np,json,sys
def calc(v):
    z=np.load(v+'/trace.npz',allow_pickle=True); r=json.load(open(v+'/run.json'))
    n=min(len(z['up_t']),len(z['up_arms']),len(z['up_meas']))
    t=z['up_t'][:n];a=z['up_arms'][:n];m=z['up_meas'][:n];S=z['states']
    out={}
    for name,t0,t1 in r['phases']:
        idx=np.where((t>=t0)&(t<=t1))[0]
        k=idx[idx>0]
        e=np.nanmax(np.abs(a[k-1]-m[k]),axis=1) if len(k) else np.array([])
        e=e[~np.isnan(e)]
        s=S[(S[:,0]>=t0)&(S[:,0]<=t1)]
        # nlim delta: from last state before t0 to last state in phase
        pre=S[S[:,0]<t0]; base=s[0,6] if len(s) else 0
        cl=(s[-1,6]-base) if len(s) else 0
        tilt=np.nanmax(s[:,2]) if len(s) else np.nan; pz=np.nanmin(s[:,1]) if len(s) else np.nan
        fall=int(((s[:,2]>35)|(s[:,1]<0.45)).sum()) if len(s) else 0
        out[name]=dict(ticks=len(idx),fall_ticks=fall,tilt_max=round(float(tilt),2),pelvis_min=round(float(pz),4),
          clipped_ticks=int(cl),clipped_pct=round(100*cl/max(len(idx),1),2),pct_err_gt_012=round(100*float((e>0.12).sum())/max(len(e),1),2))
    return out,r
if __name__=='__main__':
    o,r=calc(sys.argv[1]);print(json.dumps(o)[:300] if len(sys.argv)<3 else '')
    if len(sys.argv)>2: json.dump(o,open(sys.argv[2],'w'),indent=1)
    for k,v in o.items(): print(k,v)
