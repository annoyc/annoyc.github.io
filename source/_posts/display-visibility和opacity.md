---
title: 'display,visibility和opacity'
date: 2020-09-09 16:43:48
categories:
    - CSS
tags:
    - 面试题
---
以下为三者设置样式的区别

<!--more-->
**display: none**
1. DOM结构：浏览器不会渲染display属性为none的元素，不占用空间
2. 事件监听：无法进行DOM事件监听
3. 性能：动态改变此属性会引起重排，性能较差
4. 继承：不会被子元素继承，毕竟子类也不会被渲染
5. transition：transition不支持display

**visibility: hidden**
1. DOM结构：元素被隐藏，但是会被渲染，不会消失，占用空间
2. 事件监听：无法进行DOM事件监听
3. 性能：动态改变此属性会引起重绘，性能较高
4. 继承：会被子元素继承，自元素可以通过设置visibility: visible来取消隐藏
5. transition：visibility会立即显示，隐藏时会延时

**opacity: 0**
1. DOM结构：透明度为100%，元素隐藏，占用空间
2. 事件监听：可以进行DOM事件监听
3. 性能：提升为合成层，不会触发重绘，性能较高
4. 继承：会被子元素继承且子元素不能通过opacity: 1来取消隐藏
5. transition：opacity可以延时显示和隐藏