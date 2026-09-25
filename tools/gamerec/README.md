# tools/gamerec — 已迁移，这里只剩兼容转发

录制器（ScreenCaptureKit 音视频采集）的**唯一实现**已经移到独立仓库：

**`agent-capture`** → `tools/macos/`（`main.swift`、`build.sh`、`record-game.sh`、`tests/`）

本目录**不再维护录制器源码**。`record-game.sh` 现在只是一个转发脚本，
把旧的 `GAME_BUNDLE_ID` 命令行原样转给 agent-capture 里的实现。

## 为什么不留两份

两份实现会在"哪一份才是真的"上产生分歧，而录制这种场景里，
两份都能"看起来成功"——分歧最难被发现。所以只留一份，
旧入口只做转发。

## 怎么用

```bash
# 旧入口（转发；需要能找到 agent-capture）
export AGENT_CAPTURE_ROOT=/path/to/agent-capture
GAME_BUNDLE_ID=com.example.Game ./record-game.sh preflight

# 新入口（推荐：单窗口、画面与音频分开指定、持久 run 状态、后台 worker）
python3 "$AGENT_CAPTURE_ROOT/scripts/agent_capture.py" capability
python3 "$AGENT_CAPTURE_ROOT/scripts/agent_capture.py" targets
```

## 边界（沿用原实现，未变）

- 只录**指定目标**的 app 级原声，**不录麦克风**、**不录其它应用**
- **没有默认目标**：不给选择器直接失败，绝不回退录整屏
- 默认**拒绝覆盖**已有素材
- `verify` 只报它真正测到的东西，并打印它**不**证明什么（音画同步等）
