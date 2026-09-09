# 附近发现 · NearbyGo

基于用户当前实时位置的 H5 吃喝玩乐推荐助手。H5 在获得用户授权后获取当前位置，后端将坐标和问题发送到 Dify Cloud Chatflow；Chatflow 调用本服务封装的高德 POI 与路径规划接口，再由 DeepSeek 生成有依据的附近推荐。

推荐范围不绑定城市或固定地点，而是始终以用户本次定位为中心，并结合预算、距离、出行方式和个人偏好筛选餐饮、咖啡、娱乐、购物、景点等场所。

## 已包含

- 移动端 H5 聊天页面
- 浏览器实时定位与定位授权状态提示
- 麦克风录音转文字，以及 Dify 语音流/浏览器双层回答朗读
- Dify Cloud SSE 流式聊天代理，避免前端泄露 Dify Key
- Dify reasoning 双层过滤，只向页面展示最终答案
- 推荐过程中实时展示处理进度，多地点路线使用受控并发查询以缩短等待时间
- 浏览器本地保存最近 12 轮对话，可随时清空且不保存定位
- Dify 最近 8 轮短期记忆，支持理解“还是上次那样”“预算不变”等上下文承接
- 用户明确授权才写入的会话级长期画像，支持记住、忘记指定项和清空全部偏好
- Dify 需求分流保留附近实时推荐与日常问答两条分支，普通问题不会调用定位或高德接口
- GPS → 高德坐标转换
- 高德周边 POI 搜索
- 餐饮与游玩分组检索，复合需求按“当前位置 → 餐饮 → 游玩”分段计算路线
- 按总时间预算分配交通、用餐、游玩和机动时间，避免把短途步行当作完整行程
- 支持最多 7 天的分日攻略；每天包含主题、相对时间段、逐站停留建议和路线衔接
- 预算、距离、偏好和评分的确定性排序
- 同行人、饮食/可达性、氛围、避雷项与决策偏好的细粒度需求理解
- 快速选择、候选对比、单日路线和多日攻略四种自适应回答形态
- HTTP/JSON、空结果、定位回退和避雷项冲突的回答前可信度审计
- 安全 Markdown 渲染与高德一键导航链接（移动端优先尝试唤起 App）
- 服务端签名的高德静态地图、候选点标注和行程顺序连线，不向浏览器泄露高德 Key
- 日常问答与附近出行/美食独立分支，普通问题不传递定位也不调用高德
- Dify Chatflow DSL 和配置文档

## 所需凭据

真实 Key 和本地 `.env` 文件不得提交到 Git。仓库中的 `.env.example` 只提供变量名和非敏感默认值；请使用 Dify、部署平台或服务器的 Secret 管理功能保存真实凭据：

1. Dify Cloud Chatflow 应用 API Key
2. 高德开放平台 **Web 服务 API Key**
3. DeepSeek API Key（只配置在 Dify Cloud 模型供应商中）
4. 自行生成的 `INTERNAL_API_TOKEN`

当前 H5 通过服务端代理高德静态地图，复用同一个 **Web 服务 API Key**，不需要高德 JS API Key 或安全密钥。

## 本地启动

```bash
# 每位开发者首次运行时复制模板，并在 .env 中填写自己的真实凭据。
cp .env.example .env
docker compose up --build
```

Docker Compose 会自动读取仓库根目录的 `.env`。至少填写 `DIFY_API_KEY`、`AMAP_WEB_SERVICE_KEY` 和 `INTERNAL_API_TOKEN`；其中 `INTERNAL_API_TOKEN` 必须与 Dify Chatflow 中的同名变量一致。`.env` 已被 Git 忽略，不得强制提交。

打开 `http://localhost:8000`。浏览器精确定位在生产环境需要 HTTPS；localhost 通常可用于本地开发。

## Render 部署

仓库根目录已包含 `render.yaml`。在 Render Dashboard 选择 **New → Blueprint**，连接本 GitHub 仓库并确认 Blueprint，然后填写三个不会进入仓库的秘密值：

- `DIFY_API_KEY`：已发布 Chatflow 的应用 API Key。
- `AMAP_WEB_SERVICE_KEY`：高德 Web 服务 Key。
- `INTERNAL_API_TOKEN`：自行生成的高强度随机字符串。

部署成功后：

1. 打开 `https://<你的服务名>.onrender.com/api/health`，确认 `dify`、`amap`、`internal_token` 均为 `true`。
2. 在 Dify Chatflow 环境变量中把 `BACKEND_BASE_URL` 改成这个 Render HTTPS 地址，不要带末尾 `/`。
3. 把 Dify 中的 `INTERNAL_API_TOKEN` 设为与 Render 完全相同的值，然后重新发布 Chatflow。
4. 打开 Render 服务首页，授权定位和麦克风，分别测试日常问答、附近推荐、地图和语音。

如果已经在 Render 上手动创建了同名 `nearby-go-2` 服务，可以继续使用它：在 Settings 中保持 Dockerfile Path 为 `backend/Dockerfile`、Docker Build Context 为 `.`、Health Check Path 为 `/api/health`，并补齐上述环境变量后选择 **Manual Deploy → Deploy latest commit**。

不使用 Docker：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

## Dify 配置

按照 [dify/SETUP.md](dify/SETUP.md) 导入 DSL、配置 DeepSeek，并在 Dify Cloud 中检查两个 Chatflow 环境变量。DSL 包含公开后端地址和空的 Token 占位符，不包含真实秘密值。

## 关键接口

- `GET /api/health`：配置状态
- `POST /api/chat`：H5 调用的 Dify SSE 代理
- `POST /api/audio-to-text`：H5 录音转文字代理，Dify Key 不进入浏览器
- `GET /api/route-map`：签名路线点对应的高德静态地图图片
- `POST /api/recommendations`：Dify HTTP 节点调用的高德推荐接口，需要 `X-Internal-Token`

推荐接口示例：

```json
{
  "longitude": 121.4737,
  "latitude": 31.2304,
  "coordinate_system": "gps",
  "categories": ["美食", "景点", "公园"],
  "keywords": ["川菜"],
  "preferences": ["辣"],
  "budget_per_person": 80,
  "radius_meters": 3000,
  "transport": "walking",
  "duration_minutes": 180,
  "duration_days": 1,
  "result_count": 3
}
```

行程响应中的实时路线耗时与建议停留时间严格区分：路线字段来自高德；`itinerary_days` 中的停留时长是根据用户总时间预算和地点类别生成的规划建议。开放时间、门票、预约、排队、住宿和现场活动不在当前数据源内，不会被当作已知事实。

## 安全边界

- Dify Key、高德 Web 服务 Key、DeepSeek Key 均不进入浏览器。
- POI 返回文本被视为不可信数据；可信度审计会移除内部排序分和非必要坐标，最终提示词禁止依据外部文本改变系统规则。
- 定位不写数据库；当前只随单次聊天请求转发。
- 长期画像保存在 Dify 当前会话变量中，可能包含用户主动要求记住的饮食或可达性偏好；它会随同一 `conversation_id` 跨页面刷新继续使用，但不会跨设备或自动进入新对话。
- 页面“清空对话”会丢弃本地会话 ID并开始空白新会话；如需清空当前 Dify 会话内已保存的画像，应先对助手说“清空长期偏好”。Dify 服务端对旧会话的实际保留期限由工作区数据策略决定。
- 上线前应补充用户同意说明、请求限流、日志脱敏和 Key 轮换。
