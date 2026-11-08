# sublime_text channels mirror

Sublime Text Package Control 通道 `channel_v3.json` / `channel_v4.json` 的镜像，官方内容与本地自定义内容去重合并后推送 Codeberg。

官方源（每天定时拉取）：

- https://packagecontrol.github.io/channel/channel_v4.json
- https://packagecontrol.io/channel/channel_v3.json

## 加速访问地址

```
https://codeberg.org/tekintian/sublime_text-channels/raw/branch/main/channel_v3.json
https://codeberg.org/tekintian/sublime_text-channels/raw/branch/main/channel_v4.json
```

Package Control 中通过 `Add Channel` 填入上面的 raw 地址即可。

## 仓库结构

```
channel_v3.json            # 生成物：官方 + 自定义合并后的镜像（请勿手工修改）
channel_v4.json            # 生成物：官方 + 自定义合并后的镜像（请勿手工修改）
upstream/channel_v3.json   # 生成物：官方原始快照，用于失败兜底与差异审计
upstream/channel_v4.json
overrides/channel_v3.json  # 手工维护：自定义包/仓库，CI 只读不覆盖
overrides/channel_v4.json
scripts/sync_channels.py   # 拉取 + 合并 + 去重的同步脚本（仅 Python 标准库）
.github/workflows/sync.yml # 每天定时执行并推送 Codeberg
```

## 自定义内容

自定义包写在 `overrides/channel_v*.json`，结构与官方文件一致：

- `repositories`：自定义仓库地址，会排在官方仓库之前；
- `packages_cache`：按仓库地址分桶，桶内是包对象数组；自定义桶**排在前面**，同名包**覆盖官方条目**；
- `dependencies_cache` / `libraries_cache`：同 `packages_cache` 规则。

当前自定义内容：`MySideBar`（`https://github.com/tekintian/MySideBar`），仓库地址为 `https://raw.githubusercontent.com/tekintian/MySideBar/main/packages.json`，该地址同时写入 `repositories` 并作为 `packages_cache` 的桶键，已并入 `channel_v3.json` 与 `channel_v4.json`。

## CI 定时同步

`.github/workflows/sync.yml` 每天 UTC 02:00（北京时间 10:00）执行，也支持手动触发。

配置（本工作流使用 **Environment `codeberg-mirror`**，secret 需配在该环境下）：

- Environment `codeberg-mirror` 下的 Secret `CODEBERG_SSH_KEY`：Codeberg 账户的 SSH 私钥（ed25519 格式，需把对应公钥加到 Codeberg 账号的 SSH Keys 中）；
- 该环境下的变量 `CODEBERG_REPO`：目标仓库，默认 `tekintian/sublime_text-channels`，兼容 `owner/repo`、`ssh://git@codeberg.org/owner/repo.git`、`git@codeberg.org:owner/repo.git` 与 HTTPS 地址。

工作流会用 `ssh-keyscan` 预置 `codeberg.org` 主机指纹，避免首次连接的交互确认；推送使用 `--force`，确保镜像与生成结果完全一致。

同步流程：拉取官方源（带重试、完整性校验、非严格 JSON 修复）→ 合并 `overrides/` → 校验产物（条目数骤降 20% 以上即拒绝发布）→ 有变化才提交 → SSH 推送 Codeberg。

任一官方源拉取失败时，自动回退到 `upstream/` 快照继续合并，镜像不会变成空文件。