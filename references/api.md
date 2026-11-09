# DeepSeek Search / Fetch 接口

核对日期：2026-10-28。以官方文档的最新版本为准：
- https://api-docs.deepseek.com/zh-cn/guides/search/
- https://api-docs.deepseek.com/zh-cn/api/search/
- https://api-docs.deepseek.com/zh-cn/api/fetch/

两个接口都是 POST JSON，Authorization 为 Bearer API key。

| 接口 | 请求参数 | 响应 |
| --- | --- | --- |
| `/v1/tools/search` | `query`：1–128 Unicode 字符且非全空白；`count`：1–50，默认 10；`need_content`：默认 false；可选 `ori_question`：1–128 字符；`localization`：本地化 IP，默认空字符串 | `results` 数组；元素含 title、url、snippet、summary、content、site_name、site_icon、date、authority、rerank_score |
| `/v1/tools/fetch` | `url`：最长 8192 字符，HTTP/HTTPS 且有 host，不得携带用户名或密码 | `title` 与 Markdown `content` |

`date` 为 UTC RFC3339 或 null。`authority` 为 -1 至 4，-1 未知；`rerank_score` 为 0–3。这些字段由搜索服务产生。

核对时 Search / Fetch 均为每 1000 次成功请求 5 元；有结果才计费。Search 无结果、Fetch 无正文、失败或超时不计费。count 不改变单次价格。直接调用与通过模型 Server 端工具调用的收费时间说明不同，不能据后者推断直接接口免费。大规模运行前重新核价。

账号级限速：Search 100 QPS，Fetch 25 QPS；Fetch 还受目标域名限速约束。脚本串行调用，不实现批量爬取。

| 状态 | 处理 |
| --- | --- |
| 400 | 修正请求参数 |
| 401 | 让用户核查 key；不打印鉴权头 |
| 402 | 余额不足，停止 |
| 403 | Fetch 安全策略拒绝，停止 |
| 404 | Fetch 无可用正文，记录缺口 |
| 408 / 429 / 5xx | 等待后有界重试 |

调用脚本支持 `--localization`，仅传用户确实需要的 IP；不自动探测或上传本机公网 IP。
