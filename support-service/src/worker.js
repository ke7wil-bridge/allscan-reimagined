const REPO = 'ke7wil-bridge/allscan-reimagined'
export const LABELS = { bug: ['bug','asr-report'], question: ['question','asr-support'], feature: ['enhancement','asr-feedback'] }
const LIMIT = 30000
export function redact(value='') {
  return String(value)
    .replace(/-----BEGIN [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----/gi,'[REDACTED PRIVATE KEY]')
    .replace(/(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+/gi,'$1[REDACTED]')
    .replace(/((?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|github[_-]?token|tgif[_-]?(?:password|token)|password|passwd|secret|cookie|session|phpsessid)\s*[:=]\s*)[^\s&;]+/gi,'$1[REDACTED]')
}
function cors() { return {'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'POST,OPTIONS','Access-Control-Allow-Headers':'Content-Type'} }
function json(body,status=200){return new Response(JSON.stringify(body),{status,headers:{...cors(),'Content-Type':'application/json'}})}
function b64url(bytes){return btoa(String.fromCharCode(...bytes)).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_')}
async function importKey(pem){
  const raw=Uint8Array.from(atob(pem.replace(/-----[^-]+-----|\s/g,'')),c=>c.charCodeAt(0))
  return crypto.subtle.importKey('pkcs8',raw,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['sign'])
}
async function appJwt(env){
  const now=Math.floor(Date.now()/1000), header=b64url(new TextEncoder().encode(JSON.stringify({alg:'RS256',typ:'JWT'})))
  const payload=b64url(new TextEncoder().encode(JSON.stringify({iat:now-30,exp:now+540,iss:env.GITHUB_APP_ID})))
  const input=`${header}.${payload}`, key=await importKey(env.GITHUB_APP_PRIVATE_KEY)
  return `${input}.${b64url(new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',key,new TextEncoder().encode(input))))}`
}
async function installationToken(env){
  const jwt=await appJwt(env)
  const r=await fetch(`https://api.github.com/app/installations/${env.GITHUB_INSTALLATION_ID}/access_tokens`,{method:'POST',headers:{Authorization:`Bearer ${jwt}`,Accept:'application/vnd.github+json','User-Agent':'ASR-Support'}})
  if(!r.ok) throw new Error('GitHub App token request failed')
  return (await r.json()).token
}
async function rateLimit(request,env){
  const ip=request.headers.get('CF-Connecting-IP')||'unknown', hour=Math.floor(Date.now()/3600000), key=`rate:${ip}:${hour}`
  const count=Number(await env.RATE_LIMIT.get(key)||0)
  if(count>=5) return false
  await env.RATE_LIMIT.put(key,String(count+1),{expirationTtl:3700}); return true
}
export function validate(body){
  if(!body||!LABELS[body.kind]) return 'Invalid support type.'
  if(body.website) return 'Rejected.'
  if(typeof body.content!=='string'||body.content.trim().length<3) return 'Please provide a description.'
  if(body.content.length>LIMIT) return 'Submission is too large.'
  if(body.screenshots !== undefined && !Array.isArray(body.screenshots)) return 'Invalid screenshots.'
  if((body.screenshots||[]).length>3) return 'Too many screenshots.'
  for(const shot of body.screenshots||[]){
    if(!shot || !['image/png','image/jpeg','image/webp'].includes(shot.type) || typeof shot.data!=='string') return 'Invalid screenshot.'
    if(shot.data.length>2800000) return 'Screenshot is too large.'
  }
  return ''
}
async function storeScreenshots(body,env){
  const urls=[]
  for(const shot of body.screenshots||[]){
    const ext=shot.type==='image/png'?'png':shot.type==='image/webp'?'webp':'jpg'
    const key=`support/${new Date().toISOString().slice(0,10)}/${crypto.randomUUID()}.${ext}`
    const bytes=Uint8Array.from(atob(shot.data),c=>c.charCodeAt(0))
    await env.ATTACHMENTS.put(key,bytes,{httpMetadata:{contentType:shot.type}})
    urls.push(`${env.ATTACHMENT_BASE_URL.replace(/\/$/,'')}/${key}`)
  }
  return urls
}
export default {async fetch(request,env){
  if(request.method==='OPTIONS') return new Response(null,{status:204,headers:cors()})
  if(request.method!=='POST') return json({ok:false,error:'POST required.'},405)
  if(!(await rateLimit(request,env))) return json({ok:false,error:'Too many submissions. Please try again later.'},429)
  let body; try{body=await request.json()}catch{return json({ok:false,error:'Invalid JSON.'},400)}
  const error=validate(body); if(error) return json({ok:false,error},400)
  let content=redact(body.content); const labels=LABELS[body.kind]
  try {
    const urls=await storeScreenshots(body,env)
    if(urls.length) content += `\n\n## Screenshots\n${urls.map((url,i)=>`![Screenshot ${i+1}](${url})`).join('\n\n')}`
  } catch { return json({ok:false,error:'Screenshot upload failed.'},502) }
  const first=content.split('\n').find(line=>line.trim()&&!line.startsWith('Type:')&&!line.startsWith('Labels:'))||'ASR Support'
  const prefix=body.kind==='bug'?'Bug':body.kind==='question'?'Question':'Feature'
  const title=`[ASR ${prefix}] ${first.replace(/^#+\s*/,'').slice(0,90)}`
  try{
    const token=await installationToken(env)
    const r=await fetch(`https://api.github.com/repos/${REPO}/issues`,{method:'POST',headers:{Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','Content-Type':'application/json','User-Agent':'ASR-Support'},body:JSON.stringify({title,body:content,labels})})
    if(!r.ok) throw new Error('GitHub issue creation failed')
    const issue=await r.json(); return json({ok:true,issueNumber:issue.number,issueUrl:issue.html_url})
  }catch{return json({ok:false,error:'Support service could not create the issue.'},502)}
}}
