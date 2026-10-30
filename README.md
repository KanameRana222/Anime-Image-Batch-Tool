# 二次元图片批处理工具箱（Anime Image Batch Tool）

一个本地运行的 Windows 图片批处理工具：在浏览器里完成批量 AI 超分、尺寸缩放、锐化和格式转换。所有图片都在本机处理，不上传云端。

> 本项目是个人工具的开源版本，不是 Real-ESRGAN 官方项目。AI 超分依赖第三方的 Real-ESRGAN ncnn-vulkan 引擎与模型，相关版权和许可证归原作者所有。

## 特性

- 批量选择图片、整个文件夹，或填写本机路径递归扫描
- Real-ESRGAN AI 超分：2x / 3x / 4x，默认使用 `realesrgan-x4plus-anime` 模型
- 指定宽度或高度缩放，另一边按原比例自适应
- 可选锐化：只对输出宽度不超过 1000px 的图片生效，避免放大图过度锐化
- 输出 JPG / PNG / WebP，可调质量
- 保留源目录结构，批量结果可打包成 ZIP 下载
- 逐张进度、成功/失败状态、单张下载
- 引擎自动探测，缺少引擎时仍可使用缩放、锐化和格式转换
- 正常情况下处理结果和上传缓存放在系统临时目录，不污染项目文件夹

## 技术栈

- Python 3.12+，标准库 `http.server`，不依赖 Web 框架
- Pillow：解码、缩放、锐化、编码
- Real-ESRGAN ncnn-vulkan：GPU 超分，通过子进程调用
- 原生 HTML / CSS / JavaScript 前端，无构建步骤

## 工作原理

```text
浏览器（static/）
   │  HTTP 127.0.0.1
   ▼
server.py（http.server）
   ├─ /api/scan、/api/upload  → SOURCES（内存中的源文件列表）
   ├─ /api/process           → 后台线程 run_job
   │      ├─ Pillow：解码 / 缩放 / 锐化 / 编码
   │      └─ Real-ESRGAN exe：固定 4 倍超分
   ├─ 临时输出目录            → /api/status、/api/preview、/api/zip
   └─ 浏览器下载              → 系统默认下载目录
```

几个关键设计：

1. **单进程、单任务**：`SOURCES` 和 `JOBS` 保存在内存里，同一时间只处理一个任务，适合个人本地使用。
2. **AI 放大固定走 4 倍模型**：Real-ESRGAN 的 x4 模型原生输出 4 倍；选择 2x/3x 时，先按 4 倍推理，再用 Lanczos 降采样到目标尺寸。
3. **锐化门槛**：只有处理后宽度不超过 1000px 才应用 3×3 反锐化卷积，避免大图放大后出现白边和振铃。
4. **浏览器下载**：单张和 ZIP 都通过 `Content-Disposition: attachment` 交给浏览器，保存位置由浏览器设置决定。
5. **临时目录**：优先使用系统临时目录；如果运行环境只允许写项目目录，会使用项目内隐藏缓存，并在启动/退出时清理。

## 项目结构

```text
.
├─ server.py              # HTTP 服务、任务调度、图片处理管线
├─ static/
│  ├─ index.html          # 页面
│  ├─ app.js              # 前端逻辑：上传、参数、轮询、下载
│  └─ style.css           # 样式
├─ start.bat              # Windows 启动脚本
├─ config.example.json    # 配置示例（复制为 config.json 后修改）
├─ requirements.txt       # Python 依赖
├─ make_test_images.py    # 生成测试占位图
├─ test_e2e.py            # 端到端测试
├─ LICENSE                # MIT
├─ README.md
├─ RealESRGAN/            # 本地放置，不提交（引擎与模型）
├─ runtime/               # 本地放置，不提交（嵌入式 Python）
├─ config.json            # 本地配置，不提交
└─ test_images/           # 测试占位图，不提交
```

## 快速开始

> 使用包含 `runtime/` 的发布包时，**不需要安装 Python 或 Pillow**；`start.bat` 会优先调用 `runtime\python.exe`。只有直接下载 GitHub 源码（仓库不包含 `runtime/`）时，才需要安装 Python 和 `requirements.txt`。

### 方式一：使用发布包（推荐，免安装）

发布包通常包含 `runtime/`、`RealESRGAN/`、`server.py`、`static/` 和 `start.bat`。解压后直接双击 `start.bat`，浏览器会自动打开。

仓库本身不提交 `runtime/` 和 `RealESRGAN/`：它们体积大、包含二进制和模型，并且有各自的许可证。维护者可以按上面的目录结构打包成 ZIP，作为 GitHub Release 附件发布。

### 方式二：源码运行（需要 Python）

1. 安装 Python 3.12+。
2. 安装依赖：`pip install -r requirements.txt`。
3. 准备 AI 引擎（可选）：从 [Real-ESRGAN Releases](https://github.com/xinntao/Real-ESRGAN/releases) 下载 Windows ncnn-vulkan 版本，把 `realesrgan-ncnn-vulkan.exe`、`models/` 和所需 DLL 放到项目内的 `RealESRGAN/`。
4. 启动：`python server.py`，或双击 `start.bat`。
5. 打开 `http://127.0.0.1:8765`。端口被占用时会自动尝试后面的端口。

不准备 `RealESRGAN/` 也能运行：缩放、锐化、格式转换可用，AI 放大选项会提示引擎缺失。

### 引擎配置

默认从项目内查找 `RealESRGAN/realesrgan-ncnn-vulkan.exe` 和 `RealESRGAN/models/`。也可以复制 `config.example.json` 为 `config.json`，填写自定义路径。路径支持相对项目根目录的写法，也支持绝对路径。

## 配置

| 字段 | 说明 | 默认值 |
|---|---|---|
| `exe` | Real-ESRGAN ncnn-vulkan 可执行文件 | `RealESRGAN/realesrgan-ncnn-vulkan.exe` |
| `model_dir` | 模型目录 | `RealESRGAN/models` |
| `model` | 模型名 | `realesrgan-x4plus-anime` |
| `tile` | 推理 tile 大小，0 为自动；显存不足可设 256/128 | `0` |
| `gpu` | GPU 编号或 `auto` | `auto` |
| `port` | 起始端口 | `8765` |
| `engine_timeout` | 单张超分超时（秒），0 为不限制 | `1800` |

可选环境变量：

- `IMGTOOL_DOWNLOAD_DIR`：覆盖默认下载目录（主要用于测试或特殊环境）。
- `IMGTOOL_NO_BROWSER=1`：启动时不自动打开浏览器。

## 使用说明

1. 选择图片来源：浏览器上传、选择文件夹，或填写本机路径。本机路径模式直接读取服务器所在机器的磁盘，适合大文件夹，不需要上传。
2. 选择尺寸处理：不调整、AI 2x/3x/4x、指定宽度或高度。
3. 可选锐化：设置强度 0–100；只有输出宽度 ≤1000px 的图片会应用锐化。
4. 选择输出格式和质量，点击开始批处理。
5. 处理完成后，逐张点击「保存」，或点击「保存全部到下载文件夹」下载 ZIP。浏览器会保存到默认下载目录；也可以在结果行点击下载。

输出文件名格式：`原名 (x2|w800|h600|原图)[ 锐化].扩展名`。源目录结构会保留在临时输出目录和 ZIP 中。

## 临时文件与保存位置

- 处理结果、上传缓存、ZIP 临时文件都放在系统临时目录下的 `imgbatch-work-<进程号>`，服务退出时清理。
- 如果系统临时目录不可写，会回退到项目内隐藏目录 `.imgbatch-cache-<进程号>`，并在启动时清理旧目录、退出时删除当前目录。
- 正常情况下项目文件夹不会生成 `处理结果/`、`.uploads/` 或 ZIP；图片通过浏览器下载。受限环境的隐藏缓存见上一条。
- 浏览器下载位置由浏览器的「下载」设置决定，通常是系统下载文件夹。

## HTTP API

本工具没有鉴权，默认只监听 `127.0.0.1`，接口仅供本机页面使用。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ping` | 引擎状态、模型名、下载目录 |
| POST | `/api/scan` | 扫描本机路径，body 为 `{path}` |
| POST | `/api/upload?sid=&name=&rel=` | 原始字节流上传单张图片 |
| POST | `/api/process` | 创建任务，body 为 `{sid, options}` |
| GET | `/api/status?job=` | 任务进度与逐张状态 |
| GET | `/api/preview?job=&i=` | 下载单张结果 |
| GET | `/api/zip?job=` | 下载全部结果的 ZIP |
| POST | `/api/open` | 用系统文件管理器打开下载目录 |

## 测试

先启动服务，再生成测试图并运行端到端测试：

```bat
python make_test_images.py
python test_e2e.py
```

`test_e2e.py` 依赖 `test_images/` 下的固定尺寸占位图；`make_test_images.py` 会生成它们。AI 测试需要可用的 Real-ESRGAN 引擎和 GPU，没有引擎时 AI 相关用例会失败，其余用例仍可运行。

端口不是 8765 时，设置环境变量：

```bat
set IMGTOOL_PORT=8766
python test_e2e.py
```

## 安全与隐私

- 服务只监听 `127.0.0.1`，没有登录、Token 或 CSRF 防护。**不要直接暴露到公网。**
- `/api/scan` 会读取服务器本机路径，`/api/open` 会打开本机文件夹，这些接口只适合本地使用。
- 上传限制：单张 200MB；打开图片限制 300MP，防止解码超大图片耗尽内存。
- 图片在本机处理，不会上传云端。若你替换成第三方 AI API，请自行评估隐私和版权。
- 如需公网部署，请在反向代理层增加 HTTPS、认证、限流和文件大小限制，并删除或限制本地路径相关接口。

## 已知限制

- 仅面向 Windows：`start.bat`、Real-ESRGAN Windows 二进制、注册表读取等；Linux/macOS 需要自行适配。
- AI 超分依赖 Vulkan GPU 和 Real-ESRGAN ncnn-vulkan，纯 CPU 环境不可用。
- 默认只带动漫模型 `realesrgan-x4plus-anime`；真人照片建议换用 `realesrgan-x4plus` 等模型。
- 2x/3x 会先做 4 倍推理再降采样，大图会占用较多内存；超大图可能失败。
- 锐化只对输出宽度 ≤1000px 生效。
- 不保留 EXIF/ICC 等元数据；CMYK 等特殊色彩模式可能无法直接保存为 WebP。
- 同一时间只处理一个任务；没有取消按钮；刷新页面会丢失当前任务视图。
- 没有账号、配额和任务持久化，不适合直接作为多用户服务。

## 借鉴与致谢

- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)：超分模型与推理思路，作者 Xintao Wang 等。
- [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN/releases)：ncnn-vulkan 可执行文件；模型和二进制不在本仓库中。
- [ncnn](https://github.com/Tencent/ncnn)：推理框架。
- [Pillow](https://python-pillow.org/)：图片解码、缩放、锐化和编码。
- [phototool.cn](https://phototool.cn/)：WebP/锐化页面交互与 3×3 反锐化算法的参考；本仓库不包含其页面资源。

本项目与上述项目没有隶属关系，仅使用或参考其公开能力与设计。

## 许可证

本仓库代码使用 MIT License，见 [LICENSE](LICENSE)。

Real-ESRGAN、ncnn、Pillow、模型权重和 `realesrgan-ncnn-vulkan` 二进制分别遵循其各自许可证。仓库不提交这些第三方二进制和模型；通过 Release 分发时请自行确认许可和再分发条件，也不要提交 `RealESRGAN/` 内的调试运行库（如 `vcomp140d.dll`）。

## 贡献

欢迎提交 Issue 和 Pull Request。提交前请：

- 不要提交 `runtime/`、`RealESRGAN/`、`download/`、`config.json`、`test_images/`、日志或处理结果；
- 运行 `python make_test_images.py` 和 `python test_e2e.py` 做基本验证；
- 修改安全相关逻辑时，在 PR 中说明影响范围。

## FAQ

**AI 放大提示引擎缺失？** 检查 `RealESRGAN/realesrgan-ncnn-vulkan.exe`、`RealESRGAN/models/` 是否存在，或修改 `config.json` 的 `exe`/`model_dir`。

**端口被占用？** 程序会自动尝试后续端口；也可以在 `config.json` 修改 `port`。

**项目里出现了隐藏的 `.imgbatch-cache-*`？** 这是系统临时目录不可写时的兜底缓存，已设置隐藏属性，启动和退出会自动清理；正常环境不会出现。

**下载的 ZIP 在哪里？** 浏览器默认下载目录；单张保存同理。程序不控制浏览器的下载路径。

