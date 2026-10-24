import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l); print(f"{r['label']:44s} t={r['t']:6.1f} rss={r['rss']:5d} pss={r['pss']:5d} uss={r['uss']:5d} anon={r['pss_anon']:5d} file={r['pss_file']:5d} vram={r['vram']:5d} alloc={r.get('t_alloc','')} {r.get('frames','')}{r.get('dtype','')}")
