# 官方上游版本锁定

记录时间：2026-09-28 UTC。所有源码来自用户指定的官方仓库；没有未知fork，没有修改上游功能代码。机器可读文件为 `../upstreams.lock.json`。

| 项目 | branch | commit SHA | tag / 源码version | 拉取时间 UTC | license |
| --- | --- | --- | --- | --- | --- |
| [gpt-researcher](https://github.com/assafelovic/gpt-researcher) | main | `0957c301ed06c2a5857b834358c7227c739041d4` | v3.7.0；0.16.0 | 2026-09-28T06:27:26.865640+00:00 | MIT |
| [searxng](https://github.com/searxng/searxng) | master | `12f8b6515ca77c3c3bc1498584950ef5daca1433` | 该提交无对应remote tag；2026.9.25+12f8b6515 | 2026-09-28T06:27:29.807703+00:00 | AGPL-3.0-or-later |
| [qwen-agent](https://github.com/QwenLM/Qwen-Agent) | main | `31a4d36d123688581a9e9744427272b33ce940e0` | 该提交无对应remote tag；0.0.34 | 2026-09-28T06:27:56.623103+00:00 | Apache-2.0 |

拉取时间取本次新建 `.git/` 的文件系统创建时间，记录方式也写入lock。tag匹配已查询官方remote而不只依赖浅克隆的本地tags。版本号与SHA分别记录；不能用相同version代替相同commit。license文件及SHA256在lock中，使用和分发应保留上游license。

## 重建

在本目录运行 `python scripts/restore_upstreams.py`。脚本只按完整commit fetch，不自动pull/升级。遇到已有不匹配/脏checkout会停止并保留它，不reset。现有checkout已逐个验证SHA、origin和干净状态。

## Windows检出差异

SearXNG含四个Windows不可物化的Linux服务器模板名。Git对象与完整commit均保留；只从Windows工作树排除它们，源码不做重命名修改：

- `utils/templates/etc/httpd/sites-available/searxng.conf:socket`
- `utils/templates/etc/nginx/default.apps-available/searxng.conf:socket`
- `utils/templates/etc/uwsgi/apps-archlinux/searxng.ini:socket`
- `utils/templates/etc/uwsgi/apps-available/searxng.ini:socket`

检出时使用稀疏规则，并仅对该次checkout设置 `-c core.protectNTFS=false` 让Git先建立含这些路径的index；没有改变全局Git配置。排除后的tracked worktree是干净的。Linux重建不排除它们。

## 容器镜像

使用 `infra/searxng/images.lock.json` 中的官方Linux/amd64 digest；SearXNG OCI revision已与源码SHA完全一致。Valkey官方9-alpine仅在首次解析时作为定位tag，真正启动按digest。`resolve_images.py`在lock存在时不会升级它。

## 仅参考组件

Qwen-Agent已拉取，未进入运行链路。Crawl4AI未拉取，保留为候选；当前没有证据需要替换GPT Researcher原生crawler。

当前冻结的是源码与候选镜像身份，不是已经通过端到端验证的完整实验baseline。
