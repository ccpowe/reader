# README 展示素材

中英文 README 分别使用四张杂志式展示图：奶油白、墨绿、大标题与错位界面局部，覆盖封面、双语阅读、视频字幕和热门榜单。英文版沿用中文构图并本地化编辑标题、说明、标注和界面标签；阅读与视频图保留中文译文作为双语结果示例。视觉方向已确认，成图仍需用户视觉验收。原始截图保留在同一目录，便于核对内容和后续更新。

## 展示图

2026-09-12 使用内置 `image_gen` 工具制作前三张展示图，2026-09-13 补充榜单图，均以本目录真机截图为输入进行 AI 辅助合成。展示图包含裁切、重排、背景、标题和标注，**不是逐像素截图**，也不作为当前源码或发布包的验收证明。生成过程可能重绘字形、换行与界面细节，核对功能和实际界面时应查看原始截图及应用。

| 文件 | 展示重点 | 输入素材 |
| --- | --- | --- |
| [hero-editorial.png](hero-editorial.png) | 品牌封面：把世界的好内容，读成自己的；今日信息流与订阅分类错位叠放 | [home.png](home.png)、[subscriptions.png](subscriptions.png)、[logo.png](logo.png) |
| [reading-editorial.png](reading-editorial.png) | 阅读场景：放大原文与译文，对照阅读模式和原网页 | [reader.png](reader.png)、[web.png](web.png)；封面为风格参考 |
| [video-editorial.png](video-editorial.png) | 视频场景：视频画面、0:40 双语字幕片段及点击跳转标注 | [youtube.png](youtube.png)；前两张展示图为风格参考 |
| [rankings-editorial.png](rankings-editorial.png) | 榜单场景：三平台入口、Hacker News 真实条目局部，以及发现与收藏的说明；未合成 Reddit 或 GitHub 的结果列表 | [rankings.png](rankings.png)；阅读展示图为风格参考 |
| [hero-editorial-en.png](hero-editorial-en.png) | 英文品牌封面；英文标题与本地化的今日、订阅界面 | [hero-editorial.png](hero-editorial.png) |
| [reading-editorial-en.png](reading-editorial-en.png) | 英文阅读场景；编辑说明为英文，正文保留英中双语示例 | [reading-editorial.png](reading-editorial.png) |
| [video-editorial-en.png](video-editorial-en.png) | 英文视频场景；编辑说明为英文，字幕卡保留英中双语示例 | [video-editorial.png](video-editorial.png) |
| [rankings-editorial-en.png](rankings-editorial-en.png) | 英文榜单场景；标题、平台说明、界面标签和评论数本地化 | [rankings-editorial.png](rankings-editorial.png) |

图片自带完整底色，根 README 以相对路径和普通 `img` 引用，不依赖 GitHub 不支持的自定义 CSS。主要功能同时以正文和图片替代文本说明，窄屏读者无需辨认图内所有小字。生成所用提示词及输入对应关系保存在 [editorial-prompts.json](editorial-prompts.json)，后续重新生成时仍需逐张检查，不能假定生成结果与原始 UI 完全一致。

## 原始截图

六张截图均来自同一台 Android 真机，原始尺寸 1280 × 2772；通过 ADB 直接截屏，未合成或改写界面内容。设备安装包为 Reader 1.0.0（versionCode 18）。本次未重新构建 APK，也未核实安装包对应的精确源码提交，因此这些图片不作为最新提交或发布包的验收证明。

采集日期为 2026-09-08，后续展示图制作没有重新采集界面。

| 文件 | 展示内容 | 来源 |
| --- | --- | --- |
| [home.png](home.png) | 今日 / blog 分类 | LangChain 博客订阅 |
| [reader.png](reader.png) | 阅读模式的原文与译文 | LangChain 的 MCP 文章 |
| [web.png](web.png) | 应用内原网页双语排版 | 同一篇 LangChain 文章 |
| [youtube.png](youtube.png) | 视频与双语字幕轴 | Let's Get Rusty：How Rust made Python packaging… |
| [subscriptions.png](subscriptions.png) | 分类与订阅管理 | 采集时账号已有订阅 |
| [rankings.png](rankings.png) | Hacker News 榜单 | 采集时榜单数据 |
| [logo.png](logo.png) | 当前 Reader 标志 | 原样复制历史路径 `docs/design/home-draft/reader-logo.png`（原文件已移除，保留此路径用于追溯素材来源） |

截图内的文章、缩略图、视频画面和网站标识属于各自来源，不代表 Reader 对第三方内容或商标拥有权利，也不包含在 Reader 项目的 MIT 授权范围内。

未来更新截图时，按这里的语义文件名替换，并记录设备、应用版本和采集日期。正文、译文和字幕应实际加载完成；不要以设计草稿、空列表或等待状态替代功能展示。原始截图变化后，检查并按需重做对应展示图，同步根 README 的图片引用、替代文本和这里的素材记录。
