import {SAMPLES,normalizeConfig,validateFile,buildRequest,labelColor,validateResult,serializableResult,requestApi} from './studio-core.mjs';
const $=id=>document.getElementById(id);
let config=null,key='',file=null,objectURL=null,result=null,controller=null,revision=0,ready=false;
const history=[];
function message(text){$('message').textContent=text;}
function buttons(){ $('run').disabled=!file||!ready||!!controller;$('cancel').disabled=!controller;$('download').disabled=!result; }
function clearResult(){result=null;$('detections').replaceChildren();$('result-json').textContent='{}';$('timings').textContent='';$('result-summary').textContent='Run detection to locate objects.';const c=$('overlay');c.getContext('2d').clearRect(0,0,c.width,c.height);buttons();}
function stop(){revision++;controller?.abort();controller=null;buttons();}
async function selectFile(chosen){
 stop();const ticket=revision;clearResult();file=null;buttons();
 if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}$('preview').hidden=true;
 let url=null;
 try{
  if(!config)throw new Error('Refresh service configuration first.');validateFile(chosen,config);
  url=URL.createObjectURL(chosen);const probe=new Image();probe.src=url;await probe.decode();
  if(ticket!==revision){URL.revokeObjectURL(url);return;}
  if(probe.naturalWidth*probe.naturalHeight>config.max_image_pixels)throw new Error('Image exceeds the pixel limit.');
  file=chosen;objectURL=url;$('preview').src=url;$('preview').hidden=false;
  $('overlay').width=probe.naturalWidth;$('overlay').height=probe.naturalHeight;
  message('Image ready. Click Run detection to upload it.');buttons();
 }catch(e){if(url)URL.revokeObjectURL(url);if(ticket===revision){message(e.message);buttons();}}
}
function draw(ctx,detections){
 ctx.lineWidth=Math.max(2,ctx.canvas.width/240);ctx.font=`${Math.max(12,ctx.canvas.width/35)}px sans-serif`;
 for(const d of detections){const b=d.box;ctx.strokeStyle=labelColor(d.label);ctx.fillStyle=labelColor(d.label);ctx.strokeRect(b.x1,b.y1,b.x2-b.x1,b.y2-b.y1);ctx.fillText(`${d.label} ${Math.round(d.confidence*100)}%`,b.x1,Math.max(16,b.y1));}
}
function renderResult(data){
 result=serializableResult(data);$('result-json').textContent=JSON.stringify(result,null,2);
 $('result-summary').textContent=`${result.count} object${result.count===1?'':'s'} detected`;
 $('timings').textContent=`Total ${result.processing_time_ms.toFixed(1)} ms | Decode ${result.timings_ms.decode.toFixed(1)} | Inference ${result.timings_ms.inference.toFixed(1)} | Postprocess ${result.timings_ms.postprocess.toFixed(1)} ms`;
 $('detections').replaceChildren();for(const d of result.detections){const tr=document.createElement('tr');for(const text of [d.label,`${(d.confidence*100).toFixed(1)}%`,[d.box.x1,d.box.y1,d.box.x2,d.box.y2].map(v=>v.toFixed(1)).join(', ')]){const td=document.createElement('td');td.textContent=text;tr.append(td);}$('detections').append(tr);}
 const c=$('overlay');c.width=result.image.width;c.height=result.image.height;draw(c.getContext('2d'),result.detections);
 history.unshift(serializableResult(result));if(history.length>20)history.pop();renderHistory();buttons();
}
function renderHistory(){ $('session-count').textContent=String(history.length);$('history').replaceChildren();for(const r of history){const li=document.createElement('li');const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent=`${r.count} objects / ${r.processing_time_ms.toFixed(1)} ms`;pre.className='result-json';pre.textContent=JSON.stringify(r,null,2);details.append(summary,pre);li.append(details);$('history').append(li);} }
async function run(){
 if(!file||!ready||controller)return;clearResult();const ticket=revision;controller=new AbortController();buttons();message('Running detection...');
 try{
  const request=buildRequest(file,{conf:Number($('conf').value),iou:Number($('iou').value),maxDetections:config.max_detections,config});
  const data=validateResult(await requestApi(request.url,{key,init:request.init,signal:controller.signal}));
  if(ticket!==revision)return;
  if(data.image.width!==$('preview').naturalWidth||data.image.height!==$('preview').naturalHeight)throw new Error('Image orientation does not match the response.');
  renderResult(data);message('Detection complete.');
 }catch(e){if(ticket===revision)message(e.message);}
 finally{if(ticket===revision){controller=null;buttons();}}
}
async function refresh(){
 const currentKey=key;ready=false;buttons();$('service-status').textContent='Checking model...';
 try{
  const next=normalizeConfig(await requestApi('/studio/config'));
  if(!config){$('conf').value=next.default_conf;$('iou').value=next.default_iou;updateRanges();}config=next;
  const [health,models]=await Promise.all([requestApi('/health/ready'),requestApi('/api/models',{key:currentKey})]);
  if(currentKey!==key)return;const model=models.find(m=>m.id==='yolov8n');ready=health.status==='ready'&&model?.ready===true;
  $('model-metadata').textContent=JSON.stringify(model||{},null,2);$('service-status').textContent=ready?'Model ready / CPU':'Model unavailable';$('model-state').textContent=ready?'Ready':'Unavailable';$('model-state').dataset.state=ready?'ready':'unavailable';
 }catch(e){if(currentKey===key){$('service-status').textContent=e.message;$('model-state').textContent='Unavailable';}}
 buttons();
}
function updateRanges(){for(const k of ['conf','iou'])$(k+'-value').textContent=Number($(k).value).toFixed(2);}
$('image-file').addEventListener('change',e=>{if(e.target.files[0])selectFile(e.target.files[0]);});
$('drop-zone').addEventListener('dragover',e=>e.preventDefault());$('drop-zone').addEventListener('drop',e=>{e.preventDefault();if(e.dataTransfer.files[0])selectFile(e.dataTransfer.files[0]);});
for(const button of document.querySelectorAll('[data-sample]'))button.addEventListener('click',async()=>{stop();const ticket=revision;try{const r=await fetch(SAMPLES[button.dataset.sample],{redirect:'error',credentials:'same-origin'});if(!r.ok)throw new Error('Sample unavailable.');const blob=await r.blob();if(ticket===revision)await selectFile(blob);}catch(e){if(ticket===revision)message('Cannot load sample image.');}});
$('run').addEventListener('click',run);$('cancel').addEventListener('click',()=>{stop();message('Stopped waiting; server work may continue.');});
$('clear').addEventListener('click',()=>{stop();file=null;if(objectURL)URL.revokeObjectURL(objectURL);objectURL=null;$('preview').removeAttribute('src');$('preview').hidden=true;$('image-file').value='';clearResult();message('');});
for(const k of ['conf','iou'])$(k).addEventListener('input',updateRanges);
$('refresh').addEventListener('click',refresh);$('auth-open').addEventListener('click',()=>{$('api-key').value='';$('auth-dialog').showModal();$('api-key').focus();});$('auth-close').addEventListener('click',()=>{$('api-key').value='';$('auth-dialog').close();});$('auth-save').addEventListener('click',()=>{stop();key=$('api-key').value.trim();$('api-key').value='';$('auth-dialog').close();refresh();});
$('auth-dialog').addEventListener('close',()=>{$('api-key').value='';});
for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>{for(const v of ['playground','models','runs']){$('view-'+v).hidden=v!==button.dataset.view;$('nav-'+v).removeAttribute('aria-current');}button.setAttribute('aria-current','page');});
function tab(json){$('overview').hidden=json;$('json-panel').hidden=!json;for(const id of ['overview-tab','json-tab']){const active=(id==='json-tab')===json;$(id).setAttribute('aria-selected',String(active));$(id).tabIndex=active?0:-1;}}
for(const id of ['overview-tab','json-tab']){$(id).addEventListener('click',()=>tab(id==='json-tab'));$(id).addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const json=e.key==='End'||(e.key!=='Home'&&id==='overview-tab');tab(json);$(json?'json-tab':'overview-tab').focus();}});}
$('clear-history').addEventListener('click',()=>{history.length=0;renderHistory();});
$('download').addEventListener('click',()=>{if(!result)return;const canvas=document.createElement('canvas');canvas.width=result.image.width;canvas.height=result.image.height;const ctx=canvas.getContext('2d');ctx.drawImage($('preview'),0,0);draw(ctx,result.detections);canvas.toBlob(blob=>{if(!blob)return;const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='detection.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},'image/png');});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'&&!$('auth-dialog').open){e.preventDefault();run();}});
window.addEventListener('pagehide',()=>{stop();if(objectURL)URL.revokeObjectURL(objectURL);key='';history.length=0;});
refresh();
