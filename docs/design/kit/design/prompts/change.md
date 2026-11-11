`app/` 是一个已经套好风格的原型。风格包在 `style/`，本产品专属组件在 `project/`，产品需求见 `PRODUCT.md`。`CHANGE.md` 写了这次要改什么（新的产品决定或风格决定），以及受影响的视图。

## 任务：只改 CHANGE.md 要求的部分
- 不改其他视图的布局、交互和外观
- 外观仍然只来自风格包和 `project/` 的类；需要新类时按 `style/DESIGN.md` §0 的规定新建并记录
- 如果 CHANGE.md 和 PRODUCT.md 的其他地方冲突，以 CHANGE.md 为准，并在报告里列出冲突的位置（不要改 PRODUCT.md）
- 示例数据要覆盖这次改动涉及的各种情况
- 完成前 `node style/checks/lint.mjs app` 修到 0；原有的测试脚本（如果有）要能通过

## 完成后
1. 截图改动涉及的视图，存 `screenshots/`
2. 写 `REPORT.md`：改了什么（数据、组件、交互）；PRODUCT.md 里和这次改动冲突的地方；风格包里不清楚的地方（① 矛盾 ② 缺失 ③ 数值）；新建的类；需要用户判断的地方；lint 结果
3. 关掉你启动的服务
