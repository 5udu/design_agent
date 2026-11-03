"""Loopback-only PP-OCRv5 server, isolated from the application's environment."""
import asyncio,logging,os,time,threading
from pathlib import Path
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK','True')
app=FastAPI(title='Local PP-OCRv5')
logger=logging.getLogger('uvicorn.error')
engine=None
lock=threading.Lock()

class Input(BaseModel):path:str

def recognize(path):
    global engine
    with lock:
        started=time.monotonic()
        if engine is None:
            from paddleocr import PaddleOCR
            # Paddle 3.3.1 oneDNN fails on this Windows CPU's PIR attributes.
            engine=PaddleOCR(text_detection_model_name='PP-OCRv5_server_det',text_recognition_model_name='PP-OCRv5_server_rec',use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,device='cpu',enable_mkldnn=False,cpu_threads=4)
        outputs=list(engine.predict(str(path)))
        data=outputs[0].json
        if callable(data):data=data()
        data=data.get('res',data)
        texts,scores,polys=data.get('rec_texts',[]),data.get('rec_scores',[]),data.get('rec_polys',[])
        items=[{'text':str(text),'confidence':float(score),'polygon':poly.tolist() if hasattr(poly,'tolist') else poly} for text,score,poly in zip(texts,scores,polys)]
        return {'status':'succeeded','models':['PP-OCRv5_server_det','PP-OCRv5_server_rec'],'items':items,'elapsed_seconds':round(time.monotonic()-started,3)}

@app.get('/health')
def health():return {'status':'ok','model_loaded':engine is not None}

@app.post('/recognize')
async def read(body:Input):
    path=Path(body.path).resolve()
    if not path.is_relative_to((ROOT/'data').resolve()) or not path.is_file():raise HTTPException(400,'只接受本项目data中的图片')
    try:return await asyncio.to_thread(recognize,path)
    except Exception as exc:
        logger.exception('Local OCR failed')
        raise HTTPException(500,type(exc).__name__) from None

if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8766)
