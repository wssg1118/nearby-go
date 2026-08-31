# Dify Cloud 配置

## 1. 配置并行智算云模型

1. 登录 Dify Cloud，进入 **Plugins / 插件市场**。
2. 安装官方 `OpenAI-API-compatible` 模型供应商。
3. 在 **Settings → Model Provider → OpenAI-API-compatible** 中添加 LLM，填写并行智算云 API Key 和 `https://llmapi.paratera.com`。
4. Dify 中的显示名称使用 `DeepSeek-V4-Flash-0731`，API endpoint 模型名称使用平台实际提供的可调用别名（当前优先尝试 `deepseek-v4-flash`）。

> API Key 只保存在 Dify 工作区凭据中，不要写入 DSL、GitHub 或环境变量示例。如果凭据验证提示 `no healthy deployments`，先从并行智算云的模型列表确认 API endpoint 模型名称；这不是工作流节点或 Render 的错误。

## 2. 导入 Chatflow

在 Dify Studio 选择 **Import DSL file**，导入 `dify/nearby-go-chatflow.yml`。

DSL 已按当前 Dify `0.7.0` 导出结构整理。导入后检查两条分支、共十个节点：

1. 开始
2. 需求分流
3. 附近实时推荐分支：提取推荐条件 → 标准化请求 → 高德附近推荐 → 结果可信度审计 → 生成推荐说明 → 回复
4. 日常问答分支：日常问答 → 日常问答回复

如果模型节点显示未配置，分别在“提取推荐条件”和“生成推荐说明”节点中重新选择 `OpenAI-API-compatible / DeepSeek-V4-Flash-0731`。

“提取推荐条件”只使用最近 8 轮短期上下文理解“换一个、预算不变、继续刚才路线”等明确承接。工作流没有长期用户画像或记忆写入节点，不保存历史地点、路线、预算或定位；当前消息始终覆盖上下文。

“需求分流”必须保留 `nearby` 和 `general` 两个出口。附近地点、行程、路线和承接追问进入实时推荐分支；明确与附近出行无关的问题进入日常问答分支，后者不会调用定位、高德或推荐接口。

## 3. 配置 Chatflow 环境变量

DSL 携带两个环境变量的定义，其中公网后端地址使用项目默认值，内部 Token 保持为空。导入完成后，在 Dify Cloud 的 Chatflow 环境变量面板中检查并填写：

- `BACKEND_BASE_URL`：本项目部署后的公网 HTTPS 地址，不要以 `/` 结尾。
- `INTERNAL_API_TOKEN`：与服务器 `.env` 中同名变量完全一致。

不得将真实 Token 写入仓库。重新导出 DSL 后，提交前必须确认 `INTERNAL_API_TOKEN` 的 `value` 为空；`BACKEND_BASE_URL` 可以保留公开的 HTTPS 地址。

Dify Cloud 无法访问 `localhost`，因此测试“高德附近推荐”节点前，后端必须先部署到公网 HTTPS 地址。

## 4. 发布并取得应用 Key

1. 点击 **Publish**。
2. 打开 **Access API / 访问 API**。
3. 创建应用 API Key。
4. 把 Key 填入服务器 `.env` 的 `DIFY_API_KEY`，不要写进 H5 JavaScript。

H5 经后端调用：

```text
POST https://api.dify.ai/v1/chat-messages
```

输入变量由后端自动传递：`longitude`、`latitude`、`coordinate_system`、`location_accuracy` 和 `fallback_location_name`。

## 5. 联调

依次验证：

1. `GET /api/health` 三项配置均为 `true`。
2. 在 Dify 调试页手动填清华默认坐标 `116.3260, 40.0030`，运行 Chatflow。
3. 打开 H5，允许定位，询问“推荐附近人均 60 的晚餐”。
4. 拒绝定位再次测试，应回退到清华大学默认位置。
5. 点击答案中的“打开高德导航”。
6. 询问“帮我安排一个吃饭加游玩的三小时路线”，确认接口数据同时包含餐饮和游玩地点，且第二段从餐饮地点出发。
7. 确认三小时攻略中的“交通＋停留＋机动”合计约 180 分钟，而不是只展示几分钟步行。
8. 询问“安排三天两夜的附近吃喝游玩攻略”，确认按天输出、每天都有明确时间预算，并同时包含餐饮和游玩地点。
9. 紧接着询问“换一个近一点的”，确认沿用当前对话条件但重新获取本轮实时地点和路线。
10. 开始一条不含承接表达的新需求，确认不会带入上一轮预算、时间或地点。
11. 模拟推荐接口错误或空结果，确认只显示友好降级说明，不生成虚构地点。
12. 询问普通问候或通识问题，确认走“日常问答”分支且不调用“高德附近推荐”。

前端采用高德官方 HTTPS URI API 跳转，不接入网页内置地图。这样既能在普通移动浏览器中通过 `callnative=1` 尝试唤起高德 App，也保留网页回退，同时不需要把 Web 服务 Key 或新增的 JS API Key/安全密钥放到浏览器。微信等限制第三方 App 唤起的内置浏览器会显示“在浏览器打开”的提示。

生产前请为 `/api/chat` 增加网关限流，并把高德/Dify 调用日志接入监控。
