---
title: undefined和null
date: 2020-09-05 02:49:41
categories:
    - JavaScript
tags:
    - 面试题
---
undefined和null出现场景总结如下：
<!--more-->
- **undefined**
  - 变量提升：只声明，未定义，其默认值为undefined
  - 严格模式下，没有明确的执行主体，this的值为undefined
  - 对象没有这个属性名，属性值为undefined
  - 对象没有这个属性名，typeof obj[item]值为字符串'undefined'
  - 函数定义形参不传值，默认值为undefined
  - 函数没有返回值（没有return语句或者return;）
  - ...
- **null**
  - 手动设置变量的值或者对象某一属性的值为null（后面再赋值）
  - 在JS的DOM元素获取中，如果没有获取到指定的元素对象，结果一般为null
  - Object.prototype._proto_的值为null
  - 正则捕获的时候，如果没有捕获到结果，默认值是null
  - ...