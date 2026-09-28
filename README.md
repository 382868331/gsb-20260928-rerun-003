# 声音后期制作工作室

多轨声音后期工作站：Python（FastAPI + numpy/scipy/soundfile）负责工程与声音处理，
React 19 + Vite 提供时间线轨道编辑。

## 已实现

- **多轨素材放置**：素材入库后在任意轨道放置，可在时间线上拖动定位（10ms 网格吸附）。
- **裁切**：拖片段左右边缘修改 `start`/`duration`，只改引用参数，**素材文件与解码缓存不变**。
- **独听 / 静音**：每轨 M/S 按钮；任一轨独听时自动只播独听轨。
- **轨道增益**：每轨音量推子；片段也有 `gain`。
- **混音导出**：后台渲染 48kHz 立体声 float WAV，进度查询、可取消，取消后不残留任何成品/半成品文件。
- **统一采样率**：导入时用 `resample_poly` 重采样到工程采样率（默认 48000Hz），素材元信息保留原始采样率。
- **削波提示**：叠加后峰值超过 [-1, 1] 时，前端明确提示峰值与“已削波”，写文件时限幅。
- **工程存取**：JSON 仅保存素材 id、位置、裁切、静音/独听/增益；重开后渲染结果逐样本一致。

## 处理链（轨道 → 输出）

```
Clip 阶段  : 按 start/duration 从缓存裁切 → clip gain（不修改素材）
Track 阶段 : 同轨片段叠加 → 静音/独听判定 → track gain
             （效果器预留插点：clip 链 / track 链，以后实现）
Master 阶段: 各轨求和 → 峰值与削波检测 → 限幅 → 原子写文件（.part → rename）
```

## 运行

```bash
# 终端 1：后端（默认数据目录 ./data，可用 STUDIO_DATA 覆盖）
./scripts/run-backend.sh

# 终端 2：前端（Vite 已把 /api 代理到 127.0.0.1:8000）
./scripts/run-frontend.sh
# 打开 http://127.0.0.1:5173
```

## 生成可辨认的人工核对声音

```bash
cd backend && ../.venv/bin/python -m studio.demo /tmp/voices
# demo_low_220.wav (44.1kHz 低频节拍型)
# demo_mid_440.wav (48kHz  中频节奏型)
# demo_high_880.wav (48kHz 高频点缀)
```

导入后分别放到 0s / 1s / 0.5s，导出后可用 FFT 核对：
0~0.5s 只有 220Hz，0.5~1.0s 是 220+880Hz 叠加，1~2s 只有 440Hz
（此核对已固化在 `backend/tests/` 中）。

## 测试

```bash
cd backend && ../.venv/bin/python -m pytest tests -q   # 14 个：引擎/裁切/工程还原/削波/取消/API 全链路
cd frontend && node node_modules/vitest/vitest.mjs run # 4 个：移动/裁切边界/M/S
cd frontend && node node_modules/typescript/bin/tsc -p tsconfig.json
cd frontend && node node_modules/vite/bin/vite.js build
```

## 目录

- `backend/studio/models.py` — 工程/轨道/片段模型（非破坏性引用）
- `backend/studio/assets.py` — 素材导入、重采样、缓存、波形峰值
- `backend/studio/engine.py` — Clip→Track→Master 渲染链与削波检测
- `backend/studio/jobs.py` — 导出任务（进度/取消/原子落盘）
- `backend/studio/main.py` — FastAPI 路由
- `frontend/src/` — React 编辑器（store 用 zustand）
- `data/`（运行时生成）— `assets/` 素材元信息、`cache/` 解码缓存、`exports/` 成品、`project.json`
