from flask import Flask, request, render_template_string, jsonify
import requests
from threading import Thread, Event, Lock
import time
import random
import string
import json

app = Flask(__name__)
app.debug = True

headers = {
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/56.0.2924.76 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8',
    'referer': 'www.google.com'
}

stop_events = {}
threads = {}
task_status = {}
MAX_THREADS = 5
active_threads_lock = Lock()
active_threads = 0

# ======================= UTIL =======================
def _truncate(s, n=1000):
    try:
        s = str(s)
    except:
        s = ''
    if len(s) <= n:
        return s
    return s[:n] + '... (truncated)'

def get_token_info(token):
    """
    Robust token inspector:
    - requests id,name,email,picture via params (safer encoding)
    - returns diagnostic info + raw response (truncated)
    - prints debug raw response to server console for deeper inspection
    """
    token = (token or "").strip()
    if not token:
        return {"token": token, "id": "", "name": "", "email": "", "picture": "", "valid": False, "error": "Empty token", "raw": ""}

    url = 'https://graph.facebook.com/me'
    params = {
        'fields': 'id,name,email,picture.width(200).height(200)',
        'access_token': token
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10, allow_redirects=True)
    except requests.RequestException as e:
        return {"token": token, "id": "", "name": "", "email": "", "picture": "", "valid": False, "error": f"Request failed: {e}", "raw": ""}

    raw_text = f"STATUS {r.status_code} | {r.text}"
    # server-side debug line
    try:
        print("DEBUG GRAPH RAW:", raw_text[:2000])
    except:
        pass

    try:
        data = r.json()
    except ValueError:
        return {"token": token, "id": "", "name": "", "email": "", "picture": "", "valid": False, "error": "Invalid JSON response", "raw": _truncate(raw_text)}

    # Graph returned an error
    if r.status_code != 200 or "error" in data:
        err = data.get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", "") 
            code = err.get("code", "")
            detail = f"{msg} (code:{code})" if msg else f"Graph API error code {code}"
        else:
            detail = str(err)
        # hints
        hint = ""
        low = (detail or "").lower()
        if "checkpoint" in low or "blocking" in low:
            hint = "Account has blocking checkpoint — must clear via Facebook login."
        elif "invalid" in low or "expire" in low:
            hint = "Token invalid/expired — get a fresh user access token."
        elif "permissions" in low:
            hint = "Missing required permission (e.g., public_profile/email)."
        return {"token": token, "id": "", "name": "", "email": "", "picture": "", "valid": False, "error": detail + (" — " + hint if hint else ""), "raw": _truncate(raw_text)}

    # Success: extract fields safely
    pic_url = ""
    pic = data.get("picture", {})
    if isinstance(pic, dict):
        pic_data = pic.get("data", {})
        if isinstance(pic_data, dict):
            pic_url = pic_data.get("url", "")

    idv = data.get("id", "") or ""
    namev = data.get("name", "") or ""
    emailv = data.get("email", "") or "Not available"

    err_hint = ""
    if not namev and idv:
        err_hint = "Name missing in response (token may be limited or privacy settings)."
    if not pic_url:
        if err_hint: err_hint += " "
        err_hint += "Profile picture not returned."

    is_valid = bool(idv)

    return {
        "token": token,
        "id": idv,
        "name": namev,
        "email": emailv,
        "picture": pic_url,
        "valid": is_valid,
        "error": err_hint,
        "raw": _truncate(raw_text)
    }

# ======================= THREAD FUNCTIONS =======================
def thread_runner(func, *args):
    global active_threads
    with active_threads_lock:
        active_threads += 1
    try:
        func(*args)
    finally:
        with active_threads_lock:
            active_threads -= 1

def send_messages(access_tokens, thread_id, mn, time_interval, messages, task_id):
    task_status[task_id] = {"running": True, "sent": 0, "failed": 0}
    try:
        while not stop_events[task_id].is_set():
            for message1 in messages:
                if stop_events[task_id].is_set(): break
                for access_token in access_tokens:
                    if stop_events[task_id].is_set(): break
                    api_url = f'https://graph.facebook.com/v15.0/t_{thread_id}/'
                    message = f"{mn} {message1}"
                    try:
                        response = requests.post(api_url, data={'access_token': access_token, 'message': message}, headers=headers, timeout=10)
                        if response.status_code == 200:
                            task_status[task_id]["sent"] += 1
                        else:
                            task_status[task_id]["failed"] += 1
                            if "rate limit" in response.text.lower(): time.sleep(60)
                    except:
                        task_status[task_id]["failed"] += 1
                    if not stop_events[task_id].is_set(): time.sleep(time_interval)
    finally:
        task_status[task_id]["running"] = False
        stop_events.pop(task_id, None)

def send_comments(access_tokens, post_id, mn, time_interval, messages, task_id):
    task_status[task_id] = {"running": True, "sent": 0, "failed": 0}
    try:
        while not stop_events[task_id].is_set():
            for message1 in messages:
                if stop_events[task_id].is_set(): break
                for access_token in access_tokens:
                    if stop_events[task_id].is_set(): break
                    api_url = f'https://graph.facebook.com/{post_id}/comments'
                    message = f"{mn} {message1}"
                    try:
                        response = requests.post(api_url, data={'access_token': access_token, 'message': message}, headers=headers, timeout=10)
                        if response.status_code == 200:
                            task_status[task_id]["sent"] += 1
                        else:
                            task_status[task_id]["failed"] += 1
                            if "rate limit" in response.text.lower(): time.sleep(60)
                    except:
                        task_status[task_id]["failed"] += 1
                    if not stop_events[task_id].is_set(): time.sleep(time_interval)
    finally:
        task_status[task_id]["running"] = False
        stop_events.pop(task_id, None)

# ======================= ROUTES =======================
@app.route('/')
def index():
    return render_template_string(TEMPLATE, section=None, result=None, task_id='')

@app.route('/section/<sec>', methods=['GET','POST'])
def section(sec):
    result = None
    task_id = ''

    correct_password = "The-Hassan"

    if request.method == 'POST':
        if sec in ['1','3'] and request.form.get('mmm') != correct_password:
            return 'Invalid key.'

        if sec in ['1','3']:
            token_option = request.form.get('tokenOption','single')
            tokens=[]
            if token_option=='single':
                tkn = (request.form.get('singleToken') or '').strip()
                if tkn: tokens.append(tkn)
            else:
                f = request.files.get('tokenFile')
                if f:
                    raw = f.read()
                    try: text = raw.decode('utf-8',errors='ignore')
                    except: text = raw.decode('latin-1',errors='ignore')
                    tokens=[t.strip() for t in text.splitlines() if t.strip()]

            messages=[]
            fmsgs=request.files.get('txtFile')
            if fmsgs:
                raw=fmsgs.read()
                try: text=raw.decode('utf-8',errors='ignore')
                except: text=raw.decode('latin-1',errors='ignore')
                messages=[m for m in text.splitlines() if m.strip()]

            task_id=''.join(random.choices(string.ascii_letters+string.digits,k=10))
            stop_events[task_id]=Event()

            global active_threads
            if active_threads>=MAX_THREADS:
                result="❌ Too many running tasks!"
            else:
                if sec=='1':
                    thread_id=request.form.get('threadId')
                    mn=request.form.get('kidx')
                    interval=int(request.form.get('time',1))
                    t=Thread(target=thread_runner,args=(send_messages,tokens,thread_id,mn,interval,messages,task_id))
                else:
                    post_id=request.form.get('postId')
                    mn=request.form.get('kidx')
                    interval=int(request.form.get('time',1))
                    t=Thread(target=thread_runner,args=(send_comments,tokens,post_id,mn,interval,messages,task_id))
                t.start()
                threads[task_id]=t
                result=f"🟢 Task Started — ID: {task_id}"

        elif sec=='2':
            token_option=request.form.get('tokenOption','single')
            tokens=[]
            if token_option=='single':
                tkn=(request.form.get('singleToken') or '').strip()
                if tkn: tokens=[tkn]
            else:
                f=request.files.get('tokenFile')
                if f:
                    raw=f.read()
                    try: text=raw.decode('utf-8',errors='ignore')
                    except: text=raw.decode('latin-1',errors='ignore')
                    tokens=[t.strip() for t in text.splitlines() if t.strip()]
            result=[get_token_info(t) for t in tokens]

    return render_template_string(TEMPLATE, section=sec, result=result, task_id=task_id)

@app.route('/stop_task',methods=['POST'])
def stop_task():
    task_id=request.form.get('task_id')
    if task_id in stop_events:
        stop_events[task_id].set()
        return jsonify({"status":"stopped","task_id":task_id})
    return jsonify({"status":"not_found","task_id":task_id})

# ======================= HTML TEMPLATE =======================
TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>❤️𝐅𝐈𝐃𝐀 𝐇𝐀𝐂𝐊𝐄𝐑 𝐖𝐄𝐁❤️</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
body { background: url('https://i.ibb.co/pvg6nSwC/images-5.jpg') no-repeat center center fixed; background-size: cover; color:white; font-family:'Courier New',monospace; text-align:center; padding:20px; overflow-x: hidden; }
h1 { font-size:30px; color:#f0f; text-shadow:0 0 10px #f0f; animation: h1Glow 2s infinite alternate;}
.button-box { margin:15px auto; padding:20px; border:2px solid #00ffff; border-radius:10px; background: rgba(0,0,0,0.6); box-shadow:0 0 15px #00ffff; max-width:90%; transition:0.3s;}
.button-box:hover { box-shadow:0 0 25px #0ff; transform: scale(1.02);}
.form-control { border:1px solid #00ffff; background:rgba(0,0,0,0.5); color:#00ffff; }
.btn-submit { background:#00ffff; color:#000; border:none; padding:12px; width:100%; border-radius:6px; font-weight:bold; margin-top:15px; transition: all 0.3s ease-in-out; }
.btn-submit:hover { transform: scale(1.08) rotate(-3deg); box-shadow:0 0 15px #0ff,0 0 30px #0ff,0 0 45px #0ff; }
.social-icons { margin-top:30px; }
.social-icons a { font-size:40px; margin: 0 20px; transition: transform 0.5s, text-shadow 0.5s; }
.social-icons a:hover { transform: scale(1.4) rotate(15deg); text-shadow:0 0 15px #fff; }
.whatsapp { color:#25D366; }
.facebook { color:#1877F2; }
.youtube { color:#FF0000; }
footer { margin-top:30px; font-size:16px; color:#00ffff; font-weight:bold; animation: footerGlow 2s infinite alternate, footerBounce 2s infinite ease-in-out; }
.small-raw { font-size:11px; color:#ddd; max-width:420px; word-break:break-word; text-align:left; display:block; }
</style>
</head>
<body>
<div class="container">
<h1>❤️𝐅𝐈𝐃𝐀 𝐇𝐀𝐂𝐊𝐄𝐑 𝐖𝐄𝐁❤️</h1>
<h2>(𝐀𝐋𝐋 𝐒𝐓𝐘𝐓𝐄𝐌)</h2>

{% if not section %}
  <div class="button-box"><a href="/section/1" class="btn btn-submit">◄ 1 – NONSTOP CONVO SERVER ►</a></div>
  <div class="button-box"><a href="/section/3" class="btn btn-submit">◄ 2 – NONSTOP POST COMMENT SERVER ►</a></div>
  <div class="button-box"><a href="/section/2" class="btn btn-submit">◄ 3 – TOKEN CHECK ACTIVE ►</a></div>
{% elif section in ['1','3'] %}
<form method="post" enctype="multipart/form-data">
  <div class="button-box">
    <select name="tokenOption" class="form-control" onchange="toggleToken(this.value)">
      <option value="single">Single Token</option>
      <option value="file">Upload Token File</option>
    </select>
    <input type="text" name="singleToken" id="singleToken" class="form-control" placeholder="Paste single token">
    <input type="file" name="tokenFile" id="tokenFile" class="form-control" style="display:none;">
  </div>
  {% if section=='1' %}
  <div class="button-box"><input type="text" name="threadId" class="form-control" placeholder="Enter Thread ID" required></div>
  {% elif section=='3' %}
  <div class="button-box"><input type="text" name="postId" class="form-control" placeholder="Enter Post ID" required></div>
  {% endif %}
  <div class="button-box"><input type="text" name="kidx" class="form-control" placeholder="Enter Name Prefix" required></div>
  <div class="button-box"><input type="number" name="time" class="form-control" placeholder="Time Interval (seconds)" required></div>
  <div class="button-box"><input type="file" name="txtFile" class="form-control" required></div>
  <div class="button-box"><input type="text" name="mmm" class="form-control" placeholder="Enter your key" required></div>
  <button type="submit" class="btn-submit">Start Task</button>
</form>
<div class="button-box" style="margin-top:20px;">
  <input type="text" id="searchTask" class="form-control" placeholder="Enter Task ID to Stop">
  <button type="button" class="btn-submit" onclick="stopTask()">Stop Task</button>
</div>

{% elif section=='2' %}
<form method="post" enctype="multipart/form-data">
  <div class="button-box">
    <select name="tokenOption" class="form-control" onchange="toggleToken(this.value)">
      <option value="single">Single Token</option>
      <option value="file">Upload Token File</option>
    </select>
    <input type="text" name="singleToken" id="singleToken" class="form-control" placeholder="Paste token">
    <input type="file" name="tokenFile" id="tokenFile" class="form-control" style="display:none;">
  </div>
  <button type="submit" class="btn-submit">Check Token</button>
</form>
{% endif %}

{% if result %}
  {% if section=='2' %}
  <table class="table table-bordered text-white mt-3">
    <tr><th>Token</th><th>Picture</th><th>ID</th><th>Name</th><th>Email</th><th>Status</th><th>Error</th><th>Raw</th></tr>
    {% for r in result %}
    <tr>
      <td style="max-width:300px;word-break:break-all">{{ r.token }}</td>
      <td>
        {% if r.picture %}
          <img src="{{ r.picture }}" style="width:48px;height:48px;border-radius:50%;" alt="pic">
        {% else %} N/A {% endif %}
      </td>
      <td>{{ r.id }}</td>
      <td>{{ r.name or 'N/A' }}</td>
      <td>{{ r.email }}</td>
      <td>{{ 'Valid' if r.valid else 'Invalid' }}</td>
      <td>{{ r.error or '-' }}</td>
      <td><pre class="small-raw">{{ r.raw }}</pre></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <div class="button-box"><pre>{{ result }}</pre></div>
  {% endif %}
{% endif %}

<footer class="footer-animated mt-3">
<h5 class="footer-title">👑 Admin Info</h5>
<p><b>Admin:</b> FIDA HUSSAIN</p>
<div class="social-icons">
<a href="https://wa.me/+923173699596" target="_blank" class="whatsapp"><i class="fab fa-whatsapp"></i></a>
<a href="https://www.facebook.com/profile.php?id=100012277818269" class="facebook"><i class="fab fa-facebook"></i></a>
<a href="https://youtube.com/@hassanshah-g5y5j?si=jbzus2qiRU0ApZSA" target="_blank" class="youtube"><i class="fab fa-youtube"></i></a>
</div>
<small>© 2025 All Rights Reserved</small>
</footer>

<script>
function toggleToken(val){
  document.getElementById('singleToken').style.display = val==='single'?'block':'none';
  document.getElementById('tokenFile').style.display = val==='file'?'block':'none';
}
function stopTask(){
  const taskId=document.getElementById('searchTask').value.trim();
  if(!taskId) return alert('Please enter Task ID');
  fetch('/stop_task',{
    method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:`task_id=${taskId}`
  }).then(res=>res.json())
  .then(data=>{
    if(data.status==='stopped') alert('Task stopped: '+data.task_id);
    else alert('Task not found: '+data.task_id);
  });
}
</script>
</body>
</html>
'''

# ======================= RUN APP =======================
if __name__=='__main__':

    app.run(host='0.0.0.0', port=5000)


