"""UI 层：主题 / 布局 / 组件 / 图标。

只负责渲染与回调入口，不写 SQL、不做业务判断。
数据流仍然是：UI → services → database。
"""

from taiplan.ui import components, icons, layout, theme  # noqa: F401

__all__ = ["components", "icons", "layout", "theme"]
