# sublime_text channels mirror

Sublime Text Package Control 通道 `channel_v3.json` / `channel_v4.json` 的国内加速镜像。

官方源（每天定时拉取，与本地自定义内容去重合并后推送 gitee）：

- https://packagecontrol.github.io/channel/channel_v4.json
- https://packagecontrol.io/channel_v3.json

## 加速访问地址

```
https://gitee.com/tekintian/sublime_text-channels/raw/main/channel_v3.json
https://gitee.com/tekintian/sublime_text-channels/raw/main/channel_v4.json
```

直链地址：

```
https://raw.giteeusercontent.com/tekintian/sublime_text-channels/raw/main/channel_v3.json
https://raw.giteeusercontent.com/tekintian/sublime_text-channels/raw/main/channel_v4.json
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
.github/workflows/sync.yml # 每天定时执行并推送 gitee
```

## 自定义内容

自定义包写在 `overrides/channel_v*.json`，结构与官方文件一致：

- `repositories`：自定义仓库地址，会排在官方仓库之前；
- `packages_cache`：按仓库地址分桶，桶内是包对象数组；自定义桶**排在前面**，同名包**覆盖官方条目**；
- `dependencies_cache` / `libraries_cache`：同 `packages_cache` 规则。

当前自定义内容：`MySideBar`（`https://github.com/tekintian/MySideBar`），已并入 `channel_v3.json`。

## CI 定时同步

`.github/workflows/sync.yml` 每天 UTC 02:00（北京时间 10:00）执行，也支持手动触发。

配置（本工作流使用 **Environment `gitee-mirror`**，secret 需配在该环境下）：

- Environment `gitee-mirror` 下的 Secret `GITEE_TOKEN`：Gitee 私人令牌（需 `projects` 权限），仅通过环境变量注入，不写入文件与日志；
- 该环境下的变量 `GITEE_REPO`：目标仓库完整地址，默认为 `https://gitee.com/tekintian/sublime_text-channels.git`；脚本会自动解析出 `owner/repo`，同时兼容 `owner/repo` 简写与 SSH 地址。

同步流程：拉取官方源（带重试、完整性校验、非严格 JSON 修复）→ 合并 `overrides/` → 校验产物（条目数骤降 20% 以上即拒绝发布）→ 有变化才提交 → 推送 gitee。

任一官方源拉取失败时，自动回退到 `upstream/` 快照继续合并，镜像不会变成空文件。

## 本地自检

```bash
./t.sh          # 离线模式跑一遍合并与校验，不会访问远程
```