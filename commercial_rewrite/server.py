import asyncio
import hashlib
import hmac
import json
import os
import traceback
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware


TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
AUTH_COOKIE = "studio_auth"
PUBLIC_PATHS = {"/login", "/api/login", "/api/auth/status"}
PUBLIC_PREFIXES = ("/static/",)


def _load_env():
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    # 本地开发读 .env；Vercel 上以 Environment Variables / -e 为准。
    load_dotenv(override=False)


_load_env()


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return default if val is None else val


def _access_password() -> str:
    return (_env("ACCESS_PASSWORD") or "").strip()


def _auth_token() -> Optional[str]:
    password = _access_password()
    if not password:
        return None
    secret = (_env("SESSION_SECRET") or password).encode("utf-8")
    return hmac.new(secret, password.encode("utf-8"), hashlib.sha256).hexdigest()


def _is_authed(request: Request) -> bool:
    expected = _auth_token()
    if expected is None:
        # 未配置 ACCESS_PASSWORD 时本地可直接访问；生产环境务必配置。
        return True
    cookie = request.cookies.get(AUTH_COOKIE, "")
    return bool(cookie) and hmac.compare_digest(cookie, expected)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


class AccessPasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _auth_token() is None or _is_public_path(request.url.path):
            return await call_next(request)
        if _is_authed(request):
            return await call_next(request)

        accept = request.headers.get("accept", "")
        if request.url.path.startswith("/api/") or "application/json" in accept:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse(url="/login", status_code=303)

def _build_system_prompt(user_prompt: str) -> str:
    # 目标：生成“像完整视频一样”的长动画，但仍可 deterministic 抓帧导出。
    # 约束：单文件、离线可运行、无外部依赖；强制 canvas + renderFrame(t)。
    return f"""
你是资深前端动效导演 + 可视化工程师。请根据主题生成一个“单文件 HTML”（必须包含完整的 <html>...</html>），效果要像一段正在播放的视频：有镜头、有节奏、有完整讲解过程，画面精致、排版严谨。

主题：
{user_prompt}

绝对硬性要求（不满足则视为失败）：
1) 只输出一个代码块，语言标记为 html：```html ... ```
2) 必须离线可运行：不要引用任何外部 CDN/字体/图片/库；不要 fetch 网络资源。
3) 画面比例 16:9，内部渲染分辨率使用 2K：WIDTH=2560, HEIGHT=1440（允许用 CSS 缩放到适配窗口）。
4) 动画总时长必须更长：DURATION 取 45~75 秒（目标 60 秒左右）。
5) 必须使用 <canvas> 2D 作为最终渲染目标，且暴露以下全局：
   - window.WIDTH（数字）= 2560
   - window.HEIGHT（数字）= 1440
   - window.DURATION（秒，数字）
   - window.renderFrame(t)：输入 t（秒，0..DURATION），以“确定性的方式”把该时刻画面渲染到 canvas。相同 t 多次调用必须得到一致画面（不要用随机数；如需噪声必须固定种子并仅依赖 t）。
6) 不要任何交互按钮；页面加载后自动播放（requestAnimationFrame 驱动），但 renderFrame(t) 仍可被外部调用抓帧。

内容与观感要求（参考“高质量科普短片”的标准）：
- 分成 9~14 个连续场景（scene），每个场景 4~7 秒，有明确“镜头语言”（推近/推远/横移/聚焦/遮罩转场），整体像视频剪辑。
- 场景转场必须是“淡入淡出”交叉混合（crossfade），禁止硬切/突然切换，也禁止“非转场时间的重影/双画面”：
  - 定义“淡入淡出基准时长” `CROSSFADE_BASE = 1.4`（秒）
  - 为避免重影/重叠过多：**实际淡入淡出时长必须按场景时长自动上限**，例如：
    - `sceneDur = currScene.end - currScene.start`
    - `CROSSFADE = min(CROSSFADE_BASE, sceneDur * 0.35)`
  - 每帧 `renderFrame(t)` 必须先清空画布（clearRect 或等效），不要复用上一帧的绘制结果
  - 严格只在转场窗口内混合两场景，其它时间只渲染一个场景：
    - 设 scenes[i] 的结束时间为 end_i，scenes[i+1] 的开始时间为 start_{{i+1}}
    - 当 t <= end_i - CROSSFADE 时：只渲染 scenes[i]（此时禁止调用/绘制 scenes[i+1]，避免任何潜在重影）
    - 当 end_i - CROSSFADE < t < end_i 时：
      - 先计算线性进度 `u = (t - (end_i - CROSSFADE)) / CROSSFADE`（u 在 0..1）
      - 再用平滑缓动得到 `u2`（例如 `smoothstep(0,1,u)` 或 `easeInOutCubic(u)`）
      - prevAlpha = 1 - u2
      - nextAlpha = u2
      - 同时渲染 scenes[i] 与 scenes[i+1]：
        - 仅当 prevAlpha > 0 时才调用 prev 的绘制函数，并在调用期间设置 globalAlpha=prevAlpha
        - 仅当 nextAlpha > 0 时才调用 next 的绘制函数，并在调用期间设置 globalAlpha=nextAlpha
    - 当 t >= end_i 时：只渲染 scenes[i+1]（此时禁止调用/绘制 scenes[i]）
  - globalAlpha 必须恢复：绘制完每个场景后把 globalAlpha 设回 1
  - 每个场景的绘制函数只负责“画自己的画面”，不要直接 clear 画布、不要改 canvas 的全局状态（除非在内部 save/restore）
- 每个场景必须包含：
  - 一个视觉主图（示意图/图表/图形系统/可视化）
  - 一个标题
  - 一段中英双语旁白字幕（两行：中文在上、英文在下）
  - 2~4 个要点（短句）
- 叙事必须完整：引入直觉 → 关键机制 → 分步演示 → 常见误区 → 一个小例子 → 总结回扣。
- 画面要“精致且一致”：统一网格（建议 12 列）、一致字号体系、统一色板（浅色背景 + 2 个强调色 + 1 个告警色）。
- 字幕与图形必须避免遮挡/穿模：底部字幕安全区不少于画面高度的 18%，并确保任何时刻字幕都可读（必要时加半透明底）。
-（不要求进度提示）不要在画面角落加入“01/12”之类的章节进度元素，以免影响排版与观感。

工程实现约束（保证稳定、可导出帧）：
- 不要使用重度滤镜导致性能崩溃；尽量用矢量形状 + 渐变 + 轻量阴影。
- 把时间轴做成纯函数：建议实现 scene 切分，例如：
  - const scenes = [{{start:0,end:4,render:(localT)=>...}}, ...]
  - renderFrame(t) 内部根据 t 路由到对应 scene，并使用统一的 easing 函数。
- 实现基础 easing（easeInOutCubic、smoothstep 等）与插值函数（lerp、clamp）。
- 字体使用系统字体栈（例如: ui-sans-serif, Segoe UI, Arial, Noto Sans）。

输出注意：
- 除代码块外不要输出任何解释文字。
"""


async def _stream_tokens_openai(prompt: str) -> AsyncGenerator[str, None]:
    from openai import AsyncOpenAI

    api_key = _env("API_KEY")
    base_url = _env("BASE_URL")
    model = _env("MODEL", "gpt-4.1-mini")

    if not api_key:
        raise RuntimeError("Missing API_KEY")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
    messages = [
        {"role": "system", "content": _build_system_prompt(prompt)},
        {"role": "user", "content": prompt},
    ]

    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        temperature=0.7,
    )
    async for chunk in resp:
        token = chunk.choices[0].delta.content or ""
        if token:
            yield token


async def _stream_tokens_poe(prompt: str) -> AsyncGenerator[str, None]:
    from openai import AsyncOpenAI

    api_key = _env("POE_API_KEY") or _env("API_KEY")
    base_url = _env("BASE_URL", "https://api.poe.com/v1")
    model = _env("MODEL", "Claude-Sonnet-4")

    if not api_key:
        raise RuntimeError("Missing POE_API_KEY")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    messages = [
        {"role": "system", "content": _build_system_prompt(prompt)},
        {"role": "user", "content": prompt},
    ]

    # Poe 的 OpenAI 兼容层在某些 bot 的流式场景下会出现兼容性问题（表现为奇怪的服务端错误）。
    # 这里改为非流式一次性取回，再在本服务端切片模拟流式输出，以提升稳定性。
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        temperature=0.7,
    )
    text = (resp.choices[0].message.content or "").strip()
    for i in range(0, len(text), 64):
        yield text[i : i + 64]


async def _stream_tokens_gemini(prompt: str) -> AsyncGenerator[str, None]:
    from google import genai

    api_key = _env("API_KEY")
    model = _env("MODEL", "gemini-2.5-pro")
    if not api_key:
        raise RuntimeError("Missing API_KEY")

    client = genai.Client(api_key=api_key)
    full_prompt = _build_system_prompt(prompt)

    # python sdk 这里不保证 async stream，使用线程池避免阻塞事件循环
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        None, lambda: client.models.generate_content(model=model, contents=full_prompt)
    )
    text = getattr(resp, "text", "") or ""
    for i in range(0, len(text), 64):
        yield text[i : i + 64]


async def llm_event_stream(prompt: str) -> AsyncGenerator[str, None]:
    provider = (_env("PROVIDER", "openai") or "openai").lower().strip()
    if provider not in {"openai", "gemini", "poe"}:
        raise RuntimeError("PROVIDER must be openai|gemini|poe")

    try:
        if provider == "gemini":
            token_iter = _stream_tokens_gemini(prompt)
        elif provider == "poe":
            token_iter = _stream_tokens_poe(prompt)
        else:
            token_iter = _stream_tokens_openai(prompt)

        async for token in token_iter:
            payload = json.dumps({"token": token}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield 'data: {"event":"[DONE]"}\n\n'
    except Exception as e:
        # 在前端可见的错误信息尽量带上类型，方便定位；详细堆栈打印到服务端日志。
        print("LLM call failed:", repr(e))
        print(traceback.format_exc())
        payload = json.dumps({"error": f"{type(e).__name__}: {str(e)}"}, ensure_ascii=False)
        yield f"data: {payload}\n\n"


app = FastAPI(title="Animation Generator (Clean-room)", version="0.1.0")
app.add_middleware(AccessPasswordMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=True,
)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index(request: Request):
    # 避免模板引擎版本差异带来的运行问题：直接返回静态 HTML。
    return FileResponse(os.path.join(TEMPLATES_DIR, "index.html"), media_type="text/html; charset=utf-8")


@app.get("/login")
async def login_page(request: Request):
    if _is_authed(request):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(os.path.join(TEMPLATES_DIR, "login.html"), media_type="text/html; charset=utf-8")


@app.get("/api/auth/status")
async def auth_status(request: Request):
    return {"ok": _is_authed(request), "required": _auth_token() is not None}


@app.post("/api/login")
async def login(req: LoginRequest, response: Response):
    expected = _auth_token()
    if expected is None:
        return {"ok": True, "required": False}
    if not hmac.compare_digest(
        hashlib.sha256(req.password.strip().encode("utf-8")).digest(),
        hashlib.sha256(_access_password().encode("utf-8")).digest(),
    ):
        return JSONResponse({"error": "密码错误"}, status_code=401)

    secure = (_env("VERCEL") == "1") or (_env("COOKIE_SECURE", "0") == "1")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=AUTH_COOKIE,
        value=expected,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE, path="/")
    return response


@app.post("/api/generate")
async def generate(req: GenerateRequest, request: Request):
    async def wrapped():
        async for chunk in llm_event_stream(req.prompt):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        wrapped(),
        headers={
            "Cache-Control": "no-store",
            "Content-Type": "text/event-stream; charset=utf-8",
            "X-Accel-Buffering": "no",
        },
    )
