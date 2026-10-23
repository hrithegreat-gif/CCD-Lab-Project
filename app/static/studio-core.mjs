// Pure detection contracts: no DOM, storage or provider integration.
export const MODELS=Object.freeze([{id:'yolov8n',name:'YOLOv8n',task:'object-detection'}]);
export const DEFAULT_CONFIG=Object.freeze({auth_required:false,max_request_bytes:5246976,max_image_bytes:5242880,max_image_pixels:16777216,default_conf:.25,default_iou:.45,max_detections:100,accepted_types:['image/jpeg','image/png','image/webp']});
export const SAMPLES=Object.freeze({person:'/studio/assets/sample-person.jpg',cat:'/studio/assets/sample-cat.jpg',coffee:'/studio/assets/sample-coffee.jpg'});
export const SAFE_PATHS=new Set(['/api/detect','/api/models','/api/models/yolov8n','/health/ready','/health/live','/studio/config']);
export class StudioError extends Error {constructor(message,status=0){super(message);this.status=status;}}
const finite=(x)=>Number.isFinite(x)&&x>=0;
export function normalizeConfig(raw){
 if(!raw||typeof raw.auth_required!=='boolean')throw new StudioError('Invalid service configuration.');
 const result={auth_required:raw.auth_required};
 for(const k of ['max_request_bytes','max_image_bytes','max_image_pixels','max_detections']){
  if(!Number.isSafeInteger(raw[k])||raw[k]<=0)throw new StudioError('Invalid service limits.');result[k]=raw[k];
 }
 if(!Number.isFinite(raw.default_conf)||raw.default_conf<.05||raw.default_conf>.95||!Number.isFinite(raw.default_iou)||raw.default_iou<.1||raw.default_iou>.95||raw.max_detections>300||raw.max_image_bytes>raw.max_request_bytes)throw new StudioError('Invalid service limits.');
 if(!Array.isArray(raw.accepted_types)||!raw.accepted_types.length||raw.accepted_types.some(t=>!DEFAULT_CONFIG.accepted_types.includes(t)))throw new StudioError('Invalid image types.');
 return {...result,default_conf:raw.default_conf,default_iou:raw.default_iou,accepted_types:[...raw.accepted_types]};
}
export function validateFile(file,config=DEFAULT_CONFIG){
 if(!(file instanceof Blob)||!config.accepted_types.includes(file.type))throw new StudioError('Choose a JPEG, PNG or WebP image.');
 if(!file.size)throw new StudioError('The image is empty.');
 if(file.size>config.max_image_bytes||file.size>config.max_request_bytes)throw new StudioError('The image exceeds the upload limit.');
 return file;
}
export function buildRequest(file,{key='',conf=.25,iou=.45,maxDetections=100,config=DEFAULT_CONFIG}={}){
 validateFile(file,config);
 if(!Number.isFinite(conf)||conf<.05||conf>.95||!Number.isFinite(iou)||iou<.1||iou>.95||!Number.isInteger(maxDetections)||maxDetections<1||maxDetections>config.max_detections)throw new StudioError('Invalid detection parameters.');
 const headers={'Content-Type':file.type};if(key)headers.Authorization=`Bearer ${key}`;
 return {url:`/api/detect?conf=${conf}&iou=${iou}&max_detections=${maxDetections}`,init:{method:'POST',headers,body:file}};
}
export function labelColor(label){let hash=0;for(const c of label)hash=(hash*31+c.codePointAt(0))>>>0;return `hsl(${hash%360}, 80%, 42%)`;}
export function scaleBoxes(detections,imageSize,displaySize){
 if(![imageSize.width,imageSize.height,displaySize.width,displaySize.height].every(v=>Number.isFinite(v)&&v>0))throw new StudioError('Invalid image dimensions.');
 const sx=displaySize.width/imageSize.width,sy=displaySize.height/imageSize.height;
 return detections.map(d=>({...d,box:{x1:d.box.x1*sx,y1:d.box.y1*sy,x2:d.box.x2*sx,y2:d.box.y2*sy}}));
}
export function validateResult(data){
 const fail=()=>{throw new StudioError('The server returned an invalid detection result.');};
 if(data?.status!=='success'||data.model!=='yolov8n'||!finite(data.processing_time_ms)||!Number.isInteger(data.image?.width)||data.image.width<1||!Number.isInteger(data.image?.height)||data.image.height<1||!Array.isArray(data.detections)||data.count!==data.detections.length||data.count>300)fail();
 const p=data.parameters;
 if(!p||!Number.isFinite(p.conf)||p.conf<.05||p.conf>.95||!Number.isFinite(p.iou)||p.iou<.1||p.iou>.95||!Number.isInteger(p.max_detections)||p.max_detections<1||p.max_detections>300||data.count>p.max_detections)fail();
 if(!['decode','inference','postprocess'].every(k=>finite(data.timings_ms?.[k])))fail();
 let previous=1;
 for(const d of data.detections){const b=d.box;
  if(!Number.isInteger(d.class_id)||d.class_id<0||d.class_id>=80||typeof d.label!=='string'||!d.label||!finite(d.confidence)||d.confidence>previous||d.confidence<p.conf||!b||![b.x1,b.y1,b.x2,b.y2].every(finite)||b.x2<=b.x1||b.y2<=b.y1||b.x2>data.image.width||b.y2>data.image.height)fail();previous=d.confidence;
 }
 return data;
}
export function serializableResult(raw){
 const d=validateResult(raw);
 return {status:d.status,model:d.model,image:{width:d.image.width,height:d.image.height},count:d.count,detections:d.detections.map(x=>({class_id:x.class_id,label:x.label,confidence:x.confidence,box:{x1:x.box.x1,y1:x.box.y1,x2:x.box.x2,y2:x.box.y2}})),parameters:{conf:d.parameters.conf,iou:d.parameters.iou,max_detections:d.parameters.max_detections},processing_time_ms:d.processing_time_ms,timings_ms:{decode:d.timings_ms.decode,inference:d.timings_ms.inference,postprocess:d.timings_ms.postprocess}};
}
export function statusMessage(status){return ({401:'Enter the correct session API key.',413:'Image exceeds the upload limit.',415:'Unsupported image type or mismatched file contents.',422:'Invalid image or detection settings.',429:'Inference capacity reached. Try again later.',503:'Model is not ready. Refresh status.',504:'Inference timed out.'})[status]||'The request failed. Please try again.';}
export async function requestApi(path,{key='',init={},signal,timeout=60000,fetcher=globalThis.fetch}={}){
 const base=path.split('?')[0];
 if(!SAFE_PATHS.has(base)||path.includes('#')||(!path.startsWith(base+'?')&&path!==base)||!['GET','POST'].includes(init.method||'GET'))throw new StudioError('Only allowlisted same-origin API paths are permitted.');
 const headers=Object.fromEntries(Object.entries(init.headers||{}).filter(([name])=>name.toLowerCase()!=='authorization'));
 if(key&&base.startsWith('/api/'))headers.Authorization=`Bearer ${key}`;
 const controller=new AbortController();const abort=()=>controller.abort();let timedOut=false;
 if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
 const timer=setTimeout(()=>{timedOut=true;abort();},timeout);
 try{
  const response=await fetcher(path,{...init,headers,signal:controller.signal,redirect:'error',credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw new StudioError(statusMessage(response.status),response.status);
  return await response.json();
 }catch(e){if(e instanceof StudioError)throw e;if(controller.signal.aborted)throw new StudioError(timedOut?'Browser wait timed out; server work may continue.':'Stopped waiting; server work may continue.',-1);throw new StudioError('Cannot reach the API or read its response.');}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
