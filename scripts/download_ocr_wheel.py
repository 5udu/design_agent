"""Download official PyPI wheel in verified ranges, then verify PyPI SHA256."""
import concurrent.futures,hashlib,json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];folder=ROOT/'data/wheels';folder.mkdir(parents=True,exist_ok=True)
metadata=folder/'paddle.metadata.json'
if not metadata.exists():
    with urllib.request.urlopen('https://pypi.org/pypi/paddlepaddle/3.3.1/json',timeout=30) as response:
        distribution=json.load(response)
    m=next(f for f in distribution['urls'] if f['filename']=='paddlepaddle-3.3.1-cp312-cp312-win_amd64.whl')
    metadata.write_text(json.dumps(m),encoding='utf-8')
else:m=json.loads(metadata.read_text(encoding='utf-8'))
destination=folder/m['filename']
if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest()==m['digests']['sha256']:
    print('Official wheel already SHA256 verified',flush=True);raise SystemExit(0)
chunk=4*1024*1024;count=(m['size']+chunk-1)//chunk
def fetch(i):
    start=i*chunk;end=min(m['size'],start+chunk)-1;path=folder/f'chunk-{i}'
    if path.exists() and path.stat().st_size==end-start+1:return path
    for attempt in range(3):
        try:
            request=urllib.request.Request(m['url'],headers={'Range':f'bytes={start}-{end}'})
            with urllib.request.urlopen(request,timeout=60) as response:
                assert response.status==206
                assert response.headers['Content-Range']==f'bytes {start}-{end}/{m["size"]}'
                data=response.read()
            assert len(data)==end-start+1
            path.write_bytes(data);print(f'Wheel chunk {i+1}/{count} verified',flush=True);return path
        except Exception:
            if attempt==2:raise
            time.sleep(1)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    files=list(pool.map(fetch,range(count)))
with destination.open('wb') as output:
    for path in files:output.write(path.read_bytes())
assert hashlib.sha256(destination.read_bytes()).hexdigest()==m['digests']['sha256'],'PyPI SHA256 mismatch'
print(json.dumps({'file':str(destination),'sha256_verified':True,'bytes':destination.stat().st_size}),flush=True)
