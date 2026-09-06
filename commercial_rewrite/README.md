# Commercial Rewrite (Clean-room)

本目录是一个**独立重写版**示例，用于在不复用原仓库代码/素材/文案的前提下，实现“一句话生成动画并导出序列帧”的核心能力。

## 免责声明

- 这里的代码仅提供工程实现范式，不构成法律意见。
- 若你计划商用现有仓库内容（根目录 `LICENSE` 为 **CC BY-NC-ND 4.0**），需要另行获得原作者的商业授权。

## 功能

- 输入一句话 → 通过 LLM 生成 **单文件 HTML 动画**（`<svg>/<canvas>` + JS 动画）
- 页面内预览（iframe `srcdoc`）
- **导出序列帧**：从动画页面按固定 FPS 截图为 PNG，并打包为 ZIP 下载

## 运行

### 1) 安装依赖

```bash
cd commercial_rewrite
python -m pip install -r requirements.txt
```

### 2) 配置环境变量

推荐使用 `.env`（最省事、最稳定）。

1) 复制模板：

```bash
copy .env.example .env
```

2) 编辑 `commercial_rewrite/.env`，填入你的 key（Gemini 示例）：

```env
PROVIDER=gemini
API_KEY=YOUR_GEMINI_API_KEY
MODEL=gemini-2.5-pro
```

Poe（OpenAI 兼容 API）示例：

```env
PROVIDER=poe
BASE_URL=https://api.poe.com/v1
MODEL=Claude-Sonnet-4
POE_API_KEY=YOUR_POE_API_KEY
```

如果你不想用 `.env`，也可以直接在当前终端里设置环境变量（Windows 示例）。

#### OpenAI 兼容（OpenAI / OpenRouter / 自建兼容网关）

```bash
set PROVIDER=openai
set API_KEY=YOUR_KEY
set BASE_URL=https://openrouter.ai/api/v1
set MODEL=anthropic/claude-3.5-sonnet
```

#### Gemini

```bash
set PROVIDER=gemini
set API_KEY=YOUR_GEMINI_KEY
set MODEL=gemini-2.5-pro
```

#### Poe（OpenAI 兼容）

```bash
set PROVIDER=poe
set BASE_URL=https://api.poe.com/v1
set MODEL=Claude-Sonnet-4
set POE_API_KEY=YOUR_POE_API_KEY
```

### 3) 启动

```bash
python start.py
```

浏览器打开 `http://127.0.0.1:8010/`

