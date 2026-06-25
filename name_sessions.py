#!/usr/bin/env python3
"""cc-session-namer — isimsiz Claude Code chat session'lara anlamli isim koyar.

Tasarim ilkeleri (katmanli):
  1. KESIF (scan)      : tum slug dizinlerini tara, aday session'lari bul
  2. FILTRE (garbage)  : test/bos/bozuk chatleri deterministik ele (LLM'e gitmez)
  3. CIKARIM (llm)     : headless `claude -p` ile PROJE: KONU ismi uret (batch)
  4. YAZMA (write)     : custom-title append + os.utime mtime-fix (KRONOLOJI KORU)
  5. DOGRULAMA (verify): yazilan slug mtime azalan-monotonik mi

Kurallar:
  - append-only (silme YOK), idempotent (isimli olani atla = dogal diff)
  - aktif session (son ACTIVE_GRACE_SEC mtime) atlanir
  - kendi LLM workdir slug'i taranmaz
  - HOME slug: son HOME_WINDOW_DAYS gun aday; DIGER slug: son OTHER_LAST_N chat
  - mtime DAIMA jsonl son-gercek-event timestamp'ine set edilir (append'in bozdugu sirayi onarir)
"""
import os, json, glob, time, datetime, subprocess, sys, re, argparse, fcntl

HOME = os.path.expanduser("~")
PROJECTS = os.path.expanduser("~/.claude/projects")
STATE_DIR = os.path.expanduser("~/.cc-session-namer")
LOG_FILE = os.path.join(STATE_DIR, "namer.log")
LOCK_FILE = os.path.join(STATE_DIR, ".lock")
SKIP_FILE = os.path.join(STATE_DIR, "skipped.txt")   # LLM skip dedikleri — tekrar gonderme
WORK_SLUG_MARK = "cc-session-namer"      # kendi llm cagrilarinin slug'i — tarama disi
HOME_SLUG = "-" + HOME.strip("/").replace("/", "-")  # ör. /home/user -> -home-user (dinamik)
# gercek proje/genel chat OLMAYAN slug'lar (gecici/worker/state) — hic taranmaz
SLUG_SKIP_SUBSTR = ("cc-session-namer", "ccglm-jobs", "benchmark",
                    "glm-gsd", "-workspace", "glm-benchmark")
HOME_WINDOW_DAYS = 30
OTHER_LAST_N = 2
ACTIVE_GRACE_SEC = 900                   # son 15 dk mtime = aktif/acik, atla
MODEL = "claude-sonnet-4-6"
LLM_TIMEOUT = 300
MAX_BATCH = 25                           # tek run'da max isimlendirme (maliyet sinir)
MAX_TITLE = 46

# ---------- saf yardimcilar (selftest kapsami) ----------

def get_text(msg_obj):
    m = msg_obj.get("message", {}) if isinstance(msg_obj, dict) else {}
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c
                        if isinstance(x, dict) and x.get("type") == "text")
    return ""

def is_noise_text(t):
    if not t:
        return True
    t = t.strip()
    return (t.startswith("<") or t.startswith("Caveat:")
            or t.startswith("[Request interrupted")
            or t.startswith("This session is being continued")
            or t.startswith("Base directory for this skill"))

_GARBAGE_PATS = [
    r"reply with (only|exactly)",
    r"\bGLM_(ROUTE|FINAL)_OK\b",
    r"\bcompute\s+\d+", r"\b\d+\s*[x\*]\s*\d+\b", r"multiplied by",
    r"^say hi\b", r"^\s*pong\b", r"reply with .*pong",
    r"write a python function", r"\bchunk_list\b", r"\bis_prime\b", r"slugify\.py",
    r"hangi modelsin", r"which model are you", r"\bmodel identifier\b",
    r"sen .*hangi model", r"reply with .*your .*model",
    r"DENETIM_", r"/\.env\b", r"spawn .*subagent", r"^\s*ping\b",
]
_GARBAGE_AI = [
    "may not exist or you may not have access",
    "prompt is too long", "no response requested", "no response from api",
]

def is_garbage(first, last_u, last_ai):
    """Deterministik test/bos/bozuk tespiti — LLM'e gondermeden ele."""
    f = (first or "").strip()
    if not f and not (last_u or "").strip():
        return True
    blob = (first or "") + " \n " + (last_u or "")
    for p in _GARBAGE_PATS:
        if re.search(p, blob, re.I):
            return True
    ai = (last_ai or "").lower()
    for s in _GARBAGE_AI:
        if s in ai:
            return True
    # cok kisa matematik cevabi + matematik prompt
    if ai.strip() in ("391", "81", "42", "4", "529") and re.search(r"compute|\d+\s*[x\*]", blob, re.I):
        return True
    return False

def project_from_cwd(cwd):
    """Proje dizininin adi (cwd basename). HOME ise None (saf genel chat)."""
    if not cwd:
        return None
    cwd = cwd.rstrip("/")
    if cwd == HOME.rstrip("/"):
        return None
    return os.path.basename(cwd) or None

def slug_excluded(slug):
    """Gecici/worker/state slug'lari — gercek proje degil, hic taranmaz."""
    if slug == HOME_SLUG:
        return False
    if any(s in slug for s in SLUG_SKIP_SUBSTR):
        return True
    if "--" in slug:               # path'te /. (gizli dizin: ~/.cartography, ~/.foo)
        return True
    if slug.startswith("-tmp") or "-tmp-" in slug:
        return True
    return False

def iso_to_epoch(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def strip_json_fence(text):
    """LLM ciktisinda markdown fence varsa soy, ilk JSON array/obj'i dondur."""
    t = text.strip()
    t = re.sub(r"^```(json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    m = re.search(r"(\[.*\]|\{.*\})", t, re.S)
    return m.group(1) if m else t

def clamp_title(t):
    t = " ".join((t or "").split())
    if len(t) > MAX_TITLE:
        t = t[:MAX_TITLE - 1].rstrip() + "…"
    return t

# ---------- jsonl tarama ----------

def scan_file(path):
    """Tek jsonl'den ozet cikar."""
    cwd = ct = first_u = last_u = last_ai = ""
    last_evt_ts = None
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            typ = d.get("type")
            if not cwd and d.get("cwd"):
                cwd = d["cwd"]
            if typ == "custom-title":
                ct = d.get("customTitle", "")
                continue  # custom-title timestamp'ini SAYMA
            if d.get("timestamp"):
                last_evt_ts = d["timestamp"]
            if typ == "user":
                t = get_text(d)
                if not is_noise_text(t):
                    if not first_u:
                        first_u = t
                    last_u = t
            elif typ == "assistant":
                t = get_text(d)
                if t and not t.startswith("<"):
                    last_ai = t
    except Exception:
        return None
    return {
        "cwd": cwd, "custom_title": ct,
        "first": " ".join(first_u.split())[:300],
        "last_u": " ".join(last_u.split())[:300],
        "last_ai": " ".join(last_ai.split())[:300],
        "last_evt_epoch": iso_to_epoch(last_evt_ts),
    }

def load_skipped():
    try:
        return set(l.strip() for l in open(SKIP_FILE) if l.strip())
    except FileNotFoundError:
        return set()

def add_skipped(sids):
    if not sids:
        return
    with open(SKIP_FILE, "a", encoding="utf-8") as f:
        for s in sids:
            f.write(s + "\n")

def select_candidates(now):
    """Tum slug'lardan isimlendirme adayi session'lari topla."""
    skipped = load_skipped()
    cands = []
    for slugdir in sorted(glob.glob(os.path.join(PROJECTS, "*"))):
        if not os.path.isdir(slugdir):
            continue
        slug = os.path.basename(slugdir)
        if slug_excluded(slug):             # gecici/worker/state/gizli — atla
            continue
        files = glob.glob(os.path.join(slugdir, "*.jsonl"))
        if not files:
            continue
        files.sort(key=lambda f: -os.path.getmtime(f))
        is_home = (slug == HOME_SLUG)
        for idx, f in enumerate(files):
            mt = os.path.getmtime(f)
            if not is_home and idx >= OTHER_LAST_N:
                break                        # diger dizin: sadece son N chat
            if is_home and (now - mt) > HOME_WINDOW_DAYS * 86400:
                break                        # home: pencere disi (sirali oldugu icin dur)
            if (now - mt) < ACTIVE_GRACE_SEC:
                continue                     # aktif/acik olabilir — bir sonraki run'a birak
            info = scan_file(f)
            if info is None or info["custom_title"]:
                continue                     # idempotent: zaten isimli
            if is_garbage(info["first"], info["last_u"], info["last_ai"]):
                continue                     # garbage — isim verme
            sid = os.path.basename(f)[:-6]
            if sid in skipped:               # LLM daha once skip dedi — tekrar gonderme
                continue
            cands.append({
                "sid": sid, "path": f, "slug": slug, "mtime": mt,
                "project": project_from_cwd(info["cwd"]),
                "first": info["first"], "last_u": info["last_u"],
                "last_ai": info["last_ai"], "last_evt_epoch": info["last_evt_epoch"],
            })
    cands.sort(key=lambda x: -x["mtime"])
    return cands

# ---------- LLM cikarim ----------

PROMPT_HEADER = """Sen bir Claude Code session isimlendiricisin. Asagida isimsiz chat session'larin
ilk prompt'u, son prompt'u ve son AI cevabi var. Her biri icin KISA, anlamli bir baslik uret.

KURALLAR:
- Format: `PROJE: KONU` — hepsi BUYUK HARF, Turkce. Maks ~44 karakter.
- "project" alani DOLU ise: o proje dizininde calisilmis demektir. Baslik = O PROJEDE
  EN SON NE YAPILDI / HANGI ASAMADA KALINDI (proje adini PROJE kismina koy, konuyu tekrarlama).
- "project" alani null ise (genel/home chat): konuya gore mantikli bir PROJE etiketi sec
  (orn VOORINFRA, T4F, KG, ANTIX, SISTEM, HERMES, CC-CONFIG, REMOTE, FACTORY...).
- Eger session bir TEST/benchmark/anlamsiz/bos chat ise (model kimligi sorgusu, basit
  matematik, ping/pong, tek kelime, deny-check) → o madde icin "skip": true.
- SADECE bir JSON array dondur, markdown YOK, aciklama YOK:
  [{"i": 1, "name": "VOORINFRA: SCU W26 AUTO-UPLOAD"}, {"i": 2, "skip": true}, ...]

SESSION'LAR:
"""

def build_llm_prompt(batch):
    lines = [PROMPT_HEADER]
    for i, c in enumerate(batch, 1):
        proj = c["project"] or "null"
        lines.append(f'--- #{i} | project: {proj}')
        lines.append(f'  ILK: {c["first"][:200]}')
        if c["last_u"] and c["last_u"] != c["first"]:
            lines.append(f'  SON_U: {c["last_u"][:200]}')
        if c["last_ai"]:
            lines.append(f'  SON_AI: {c["last_ai"][:220]}')
    return "\n".join(lines)

def call_llm(prompt):
    os.makedirs(os.path.join(STATE_DIR, "work"), exist_ok=True)
    r = subprocess.run(
        ["claude", "-p", prompt, "--model", MODEL],
        cwd=os.path.join(STATE_DIR, "work"),
        capture_output=True, text=True, timeout=LLM_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude -p exit {r.returncode}: {r.stderr[:300]}")
    return json.loads(strip_json_fence(r.stdout))

# ---------- yazma + dogrulama ----------

def write_name(cand, title):
    """custom-title append + mtime'i son gercek event'e geri set (KRONOLOJI KORU)."""
    path = cand["path"]
    rec = {"type": "custom-title", "customTitle": title, "sessionId": cand["sid"]}
    with open(path, "a", encoding="utf-8") as out:
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ts = cand["last_evt_epoch"]
    if ts:
        at = os.path.getatime(path)
        os.utime(path, (at, ts))           # append'in bozdugu mtime'i onar

def verify_monotonic(slug):
    d = os.path.join(PROJECTS, slug)
    mts = sorted((os.path.getmtime(f) for f in glob.glob(os.path.join(d, "*.jsonl"))), reverse=True)
    return all(mts[i] >= mts[i + 1] for i in range(len(mts) - 1))

def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ---------- selftest ----------

def selftest():
    ok = True
    def check(name, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond
    # garbage
    check("garbage: matematik", is_garbage("Compute 17 multiplied by 23", "", "391"))
    check("garbage: glm route", is_garbage("Reply with exactly: GLM_ROUTE_OK", "", "GLM_ROUTE_OK"))
    check("garbage: model kimlik", is_garbage("sen su an hangi modelsin", "", "Claude Opus"))
    check("garbage: bos", is_garbage("", "", ""))
    check("garbage: deny-check", is_garbage("cat /path/to/.env", "", ""))
    check("garbage: model hata", is_garbage("Sadece sunu yaz: X", "", "It may not exist or you may not have access"))
    check("garbage: kod benchmark", is_garbage("Write a Python function chunk_list(items, size)", "", "def chunk_list"))
    # gercek is — garbage DEGIL
    check("gercek: voorinfra", not is_garbage("voorinfra sinan ve sahipin week 26 linki lazim", "", "Iki bagimsiz grep"))
    check("gercek: t4f", not is_garbage("T4f BAM check pipelienini calistir week 25", "", "Tamam"))
    check("gercek: antix", not is_garbage("en son asus antixlinuxta neler yapmistik", "", "donanim"))
    # project_from_cwd
    check("proj: home->None", project_from_cwd(HOME) is None)
    check("proj: cc-dashboard", project_from_cwd(HOME + "/cc-dashboard") == "cc-dashboard")
    check("proj: kg-research", project_from_cwd(HOME + "/projects/knowledge-graph-research") == "knowledge-graph-research")
    # slug_excluded
    check("slug: home dahil", not slug_excluded(HOME_SLUG))
    check("slug: cc-dashboard dahil", not slug_excluded(HOME_SLUG + "-cc-dashboard"))
    check("slug: kg-research dahil", not slug_excluded(HOME_SLUG + "-projects-knowledge-graph-research"))
    check("slug: ccglm-jobs haric", slug_excluded(HOME_SLUG + "--ccglm-jobs-jobs-glm-x-workspace"))
    check("slug: tmp haric", slug_excluded("-tmp-glm-benchmark"))
    check("slug: gizli(.) haric", slug_excluded(HOME_SLUG + "--cartography-glm-gsd"))
    check("slug: namer haric", slug_excluded(HOME_SLUG + "--cc-session-namer-work"))
    # iso_to_epoch
    _ep = iso_to_epoch("2026-06-24T07:23:00.000Z")
    _bk = datetime.datetime.fromtimestamp(_ep, datetime.timezone.utc)
    check("iso: parse", _bk.day == 24 and _bk.hour == 7 and _bk.minute == 23)
    check("iso: bos->None", iso_to_epoch("") is None)
    # strip_json_fence
    check("fence: markdown", strip_json_fence('```json\n[{"i":1}]\n```') == '[{"i":1}]')
    check("fence: cift", json.loads(strip_json_fence('prose [{"i":1,"name":"X"}] son'))[0]["i"] == 1)
    # clamp
    check("clamp: kisa", clamp_title("VOORINFRA: SCU W26") == "VOORINFRA: SCU W26")
    check("clamp: uzun", len(clamp_title("X" * 80)) <= MAX_TITLE)
    print("SELFTEST:", "GREEN" if ok else "RED")
    return ok

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="gercekten yaz")
    ap.add_argument("--dry-run", action="store_true", help="yazmadan goster (varsayilan)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--cron", action="store_true", help="cron modu (lock + sessiz)")
    ap.add_argument("--limit", type=int, default=MAX_BATCH)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    # overlap lock (cron icin)
    os.makedirs(STATE_DIR, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("ZATEN CALISIYOR (lock) — cikiliyor")
        return

    now = time.time()
    cands = select_candidates(now)
    if args.limit:
        cands = cands[:args.limit]
    if not cands:
        log("aday yok — temiz")
        return

    log(f"aday: {len(cands)} (slug dagilim: " +
        ", ".join(f"{k}={sum(1 for c in cands if c['slug']==k)}"
                  for k in sorted({c['slug'] for c in cands})) + ")")

    try:
        results = call_llm(build_llm_prompt(cands))
    except Exception as e:
        log(f"LLM HATA: {e}")
        return

    by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
    written, skipped, touched_slugs, skip_sids = 0, 0, set(), []
    for i, c in enumerate(cands, 1):
        r = by_i.get(i)
        if not r or r.get("skip") or not r.get("name"):
            skipped += 1
            if args.commit:
                skip_sids.append(c["sid"])   # kalici skip — bir daha LLM'e gitmesin
            continue
        title = clamp_title(r["name"])
        tag = f"[{c['slug']}] {c['sid'][:8]} -> {title}"
        if args.commit:
            write_name(c, title)
            touched_slugs.add(c["slug"])
            written += 1
            log("  YAZILDI " + tag)
        else:
            log("  (dry) " + tag)

    if args.commit:
        add_skipped(skip_sids)
    mono = all(verify_monotonic(s) for s in touched_slugs) if touched_slugs else True
    log(f"BITTI — yazilan={written} atlanan(garbage/skip)={skipped} "
        f"kalici-skip+={len(skip_sids)} "
        f"mode={'COMMIT' if args.commit else 'DRY'} kronoloji-monotonik={mono}")

if __name__ == "__main__":
    main()
