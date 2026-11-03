import hashlib,json
import httpx


class OCR:
    def __init__(self,config):self.config=config
    async def read(self,path):
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        cache=self.config.data_dir/'ocr'/f'{digest}.json'
        if cache.exists():return json.loads(cache.read_text(encoding='utf-8'))
        try:
            async with httpx.AsyncClient(timeout=180,trust_env=False) as client:
                response=await client.post(self.config.ocr_url+'/recognize',json={'path':str(path.resolve())})
                response.raise_for_status()
                result=response.json()
            if result.get('status')=='succeeded':
                cache.parent.mkdir(parents=True,exist_ok=True)
                cache.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
            return result
        except Exception as exc:
            return {'status':'unavailable','model':'PP-OCRv5_server','reason':self.config.safe_error(type(exc).__name__),'items':[]}
