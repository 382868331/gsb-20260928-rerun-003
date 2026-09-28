# 声音后期制作工作室

Python 负责工程管理与声音处理，React（Vite + TypeScript）提供多轨编辑界面。
当前已完成：多轨素材放置、裁切、独听、静音、混音导出；轨道效果器链位置已在
数据模型与渲染引擎中预留（`track.effects` 与 `engine._apply_effects`）。

## 处理顺序（轨道 → 输出）

```
源文件（原始采样率，永不修改）
  → 重采样到工程采样率 + 声道统一
  → 素材裁切（in/out，非破坏）
  → 素材增益（clip gain）
  → 轨道效果器链（预留，effects 按顺序执行）
  → 轨道推子增益
  → 静音/独听裁决（有任一独听轨时只输出独听轨，静音轨始终排除）
  → 母线叠加
  → 主控增益
  → 输出范围检测（|采样| > 1.0 即超范围，报告时间区间）
  → WAV 导出（原子写入，可取消）
```

## 关键约束如何保证

- **拖动/裁切不改素材**：工程 JSON 只保存 `start / in_point / out_point / gain` 等引用参数，
  源文件复制进 `session/media/` 后保持原样；恢复裁切区间即可还原完整素材。
- **工程保存后声音一致**：保存接口原子写 `project.json`，重开后按相同参数渲染；
  有往返一致性测试（保存前/后混音逐采样 allclose）。
- **采样率统一**：素材进入引擎前用 scipy polyphase 滤波重采样到工程采样率（默认 44100Hz）。
- **叠加超范围明确提示**：导出前预检，超范围返回 409 与超范围时间区间，默认阻止导出；
  界面“检测混音”可随时查看峰值与区间，用户也可显式勾选“超范围仍导出”。
- **取消导出不留成品**：导出先写 `.export-*.wav.part` 临时文件，取消或失败时删除，
  只有完整写完才原子替换为目标文件。

## 目录

- `backend/studio/model.py` — 工程/轨道/素材模型与静音独听裁决
- `backend/studio/audio.py` — 读取、重采样、声道适配、原子导出、波形包络
- `backend/studio/engine.py` — 混音引擎与超范围检测
- `backend/studio/store.py` — 会话与 `project.json` 持久化
- `backend/studio/server.py` — 标准库 HTTP API + 可取消导出任务 + 前端静态托管
- `frontend/src/` — React 轨道编辑器（拖拽移动、两端手柄裁切、M/S、增益、导出对话框）
- `tools/make_test_sounds.py` — 生成 3 段可辨认人工核对素材到 `samples/`
- `backend/tests/` — 时间位置、混音、静音/独听、非破坏裁切、采样率、溢出、取消、往返测试

## 运行

后端（需要 numpy / scipy / soundfile）：

```bash
python3 tools/make_test_sounds.py            # 生成核对素材 samples/
cd backend && python3 run_server.py --port 8000
```

前端开发（开发服务器把 `/api` 代理到 8000）：

```bash
cd frontend
npm install        # 需要可访问 npm 源
npm run dev
```

或 `npm run build` 后由后端直接托管构建产物，访问 `http://127.0.0.1:8000/`。

## 用人工声音核对

`samples/` 下三段素材特征互不相同：

- `voice-a-low.wav`：440Hz，摩斯 A（短-长）节奏，44100Hz
- `voice-b-high.wav`：880Hz 连续音，22050Hz（验证重采样统一）
- `voice-c-pulse.wav`：1760Hz 急促脉冲，44100Hz

建议摆放：A 在 0s、B 在 1s、C 在 2s。正常增益导出后，
0.5s 处主频应为 440Hz、1.5s 为 880Hz、3.5s 为 1760Hz；
把 C 推到 +12dB 与 B 叠加时，“检测混音”会报告 2s 之后的超范围区间并阻止导出。

## 测试

```bash
cd backend && python3 -m pytest tests/ -q
```
