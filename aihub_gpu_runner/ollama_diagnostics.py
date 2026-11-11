"""Bounded, best-effort evidence only. Never a source of acceptance authority."""
import hashlib,json,time
from collections import deque

SAFE_ARGV={'/usr/bin/ollama','/bin/ollama','/usr/lib/ollama/llama-server',
    'serve','gpu-discover','--lib-dir','/usr/lib/ollama','/usr/lib/ollama/cuda_v12',
    '--list-devices','--offline','--verbose','--model','--port'}

def safe(value,key='',depth=0):
    if depth>5:return {'omitted':'depth_limit'}
    if key=='argv' and isinstance(value,list):
        return {'argc':len(value),'sha256':hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest(),
            'prefix':[v if v in SAFE_ARGV else {'redacted_sha256':hashlib.sha256(v.encode()).hexdigest(),'chars':len(v)} for v in value[:12]],
            'truncated':len(value)>12}
    if isinstance(value,dict):return {str(k)[:64]:safe(v,str(k),depth+1) for k,v in list(value.items())[:24]}
    if isinstance(value,(list,tuple)):return [safe(v,depth=depth+1) for v in value[:12]]
    if isinstance(value,str):
        return value if len(value)<=256 else {'prefix':value[:256],'sha256':hashlib.sha256(value.encode()).hexdigest(),'chars':len(value)}
    if value is None or type(value) in (bool,int,float):return value
    return {'omitted':'unsupported_type'}

class BoundedDiagnostics:
    """At most 64 events of 2KiB each; explicitly account for lost detail."""
    def __init__(self):self.events=deque(maxlen=64);self.dropped=0;self.oversized=0
    def __call__(self,predicate,**fields):
        try:
            event=safe({'predicate':predicate,'observed_at':time.time(),'monotonic':time.monotonic(),**fields})
            raw=json.dumps(event,separators=(',',':'),ensure_ascii=True).encode()
            if len(raw)>2048:
                self.oversized+=1
                event={'predicate':predicate[:128],'pid':str(fields.get('pid',''))[:32],
                    'omitted':'event_size_limit','safe_event_sha256':hashlib.sha256(raw).hexdigest()}
            if len(self.events)==self.events.maxlen:self.dropped+=1
            self.events.append(event)
        except Exception:pass
    def export(self):
        return {'schema':1,'events':list(self.events),'evicted_events':self.dropped,
            'oversized_events':self.oversized,'authority':'diagnostic_only','additional_observations':0}
