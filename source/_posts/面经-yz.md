---
title: 面经(yz)
date: 2020-09-01 17:47:45
categories:
tags:
    - 面试题
---
yz某公司部分面试题，学习记录如下
<!--more-->
1. svg是什么？
    - SVG 意为可缩放矢量图形（Scalable Vector Graphics）。
    - SVG 使用 XML 格式定义图像。
2. 什么情况下用vuex？
    - 多个组件间需要传递参数或状态时
    - 较大型项目使用
3. vue本身的更新机制了解吗？ 
    - Vue 实现响应式并不是数据发生变化之后 DOM 立即变化，而是按一定的策略进行 DOM 的更新。
    - 简单来说，Vue 在修改数据后，视图不会立刻更新，而是等同一事件循环中的所有数据变化完成之后，再统一进行视图更新。
    - 同步里执行的方法，每个方法里做的事情组成一个事件循环；接下来再次调用的是另一个事件循环。
    - nextTick：在下次 DOM 更新循环结束之后执行延迟回调。在修改数据之后立即使用这个方法，会获取更新后的 DOM。 
```javascript
//改变数据
vm.message = 'changed'
//想要立即使用更新后的DOM。这样不行，因为设置message后DOM还没有更新
console.log(vm.$el.textContent) // 并不会得到'changed'
//这样可以，nextTick里面的代码会在DOM更新后执行
Vue.nextTick(function(){
    console.log(vm.$el.textContent) //可以得到'changed'
})
```
4. computed和watch的了解？
    - computed 本质是一个惰性求值的观察者，具有缓存性，只有当依赖变化后，第一次访问 computed 属性，才会计算新的值，而 watch 则是当数据发生变化便会调用执行函数
    - 从使用场景上说，computed 适用一个数据被多个数据影响，而 watch 适用一个数据影响多个数据;
5. observer和watcher的了解？
    - Vue 响应系统，其核心有三点：observe、watcher、dep：
        - observe：遍历 data 中的属性，使用 Object.defineProperty 的 get/set 方法对其进行数据劫持；
        - dep：每个属性拥有自己的消息订阅器 dep，用于存放所有订阅了该属性的观察者对象；
        - watcher：观察者（对象），通过 dep 实现对响应属性的监听，监听到结果后，主动触发自己的回调进行响应。
```javascript
// 手动注销watch
const unwatch = app.$watch('text', {
    console.log(val);
}, {
    deep: false
})
```
6. vue3.0有什么特性？
    - [此链接内容可供参考](https://www.cnblogs.com/Rivend/p/12630779.html)
7. vue中的Object.defineProperty()有什么缺陷？
    - Object.defineProperty无法监控到数组下标的变化，导致通过数组下标添加元素，不能实时响应；
    - Object.defineProperty只能劫持对象的属性，从而需要对每个对象，每个属性进行遍历，如果，属性值是对象，还需要深度遍。Proxy可以劫持整个对象，并返回一个新的对象。
    - Proxy不仅可以代理对象，还可以代理数组。还可以代理动态增加的属性。
8. var与let、const的区别
    - var声明变量存在变量提升，let和const不存在变量提升， window可以访问到var声明的值
    - let、const都是块级局部变量
    - 同一作用域下let和const不能声明同名变量，而var可以
9. 什么是块级作用域？
    - JS中作用域有：全局作用域、函数作用域。没有块作用域的概念。ECMAScript 6(简称ES6)中新增了块级作用域。块作用域由 {} 包括，if语句和for语句里面的{}也属于块作用域。
10. js中的class是怎么实现的？
    - [此链接内容可供参考](https://blog.csdn.net/weixin_33681778/article/details/88038531)
11. js基础类型和引用类型
    - es5中基础类型包括：number，string，null，undefined，Boolean。es6新增了一种基础类型symbol,基础类型的存储是存放在栈中，原因是基础类型存储的空间很小，存放在栈（stack）中方便查找，且不易于改变
    - 引用类型是指有多个值构成的对象，也就是对象类型比如：Object,Array,Function,Data等，js的引用数据类型是存储在堆中（heap），也就是说存储的变量处的值是一个指针（point），指向存储对象的内存地址。存在堆中的原因是：引用值的大小会改变，所以不能放在栈中，否则会降低变量查询的速度
12. 哪些方法判断值的类型？
    - [此链接内容可供参考](https://www.jianshu.com/p/967d6db70437)
    1. typeof 运算符
    2. instanceof
    3. 通过Object下的toString.call()方法来判断
    4. 根据对象的contructor判断
13. instance of底层实现机制
    - 只要右边变量的 prototype 在左边变量的原型链上即可。因此，instanceof 在查找的过程中会遍历左边变量的原型链，直到找到右边变量的 prototype，如果查找失败，则会返回 false
14. 水平居中的几种方式
    - [此链接内容可供参考](https://blog.csdn.net/weixin_42291381/article/details/81624935)
15. BFC(block formatting context)
    - [此链接内容可供参考](https://zhuanlan.zhihu.com/p/25321647)
    - 使 BFC 内部浮动元素不会到处乱跑
    - 和浮动元素产生边界
16. 如何创建BFC
    1. float的值不是none。
    2. position的值不是static或者relative。
    3. display的值是inline-block、table-cell、flex、table-caption或者inline-flex
    4. overflow的值不是visible
17. 触发 BFC
    - 只要元素满足下面任一条件即可触发 BFC 特性：
        - body 根元素
        - 浮动元素：float 除 none 以外的值
        - 绝对定位元素：position (absolute、fixed)
        - display 为 inline-block、table-cells、flex
        - overflow 除了 visible 以外的值 (hidden、auto、scroll)
18. 流式布局
    - [此链接内容可供参考](https://www.cnblogs.com/zylseo/p/12599443.html)
    - [此链接内容可供参考](https://www.jianshu.com/p/4a6e5162e4ee)
19. css的选择器和对应的优先级
    - [此链接内容可供参考](https://blog.csdn.net/b954960630/article/details/79560590)
    - !important > 行内样式>ID选择器 > 类选择器 > 标签 > 通配符 > 继承 > 浏览器默认属性
20. 移动端自适应布局与字体大小自适应
    - [此链接内容可供参考](https://blog.csdn.net/w390058785/article/details/80562776)
    - vw, vh
    - 用js去计算并设置html标签的font-size大小
21. em和rem的区别
    - rem 单位翻译为像素值是由 html 元素的字体大小决定的。 此字体大小会被浏览器中字体大小的设置影响，除非显式重写一个具体单位
    - em 单位转为像素值，取决于他们使用的字体大小。 此字体大小受从父元素继承过来的字体大小，除非显式重写与一个具体单位
22. 数组遍历方法
    - [此链接内容可供参考](https://www.cnblogs.com/QuietWinter/p/9115855.html)
23. post和get
    - Get产生一个TCP数据包；Post产生两个TCP数据包。
    - 对于GET方式的请求，浏览器会把http header和data一并发送出去，服务器响应200（返回数据）；对于POST，浏览器先发送header，服务器响应100（continue），然后再发送data，服务器响应200（返回数据）；
    - GET幂等，POST不幂等(幂等是指同一个请求方法执行多次和仅执行一次的效果完全相同。)
24. 强制缓存和协商缓存
    - [此链接内容可供参考](https://blog.csdn.net/zl399615007/article/details/84534884?utm_medium=distribute.pc_relevant_t0.none-task-blog-BlogCommendFromMachineLearnPai2-1.channel_param&depth_1-utm_source=distribute.pc_relevant_t0.none-task-blog-BlogCommendFromMachineLearnPai2-1.channel_param)
25. http头部字段有哪些？
    - [此链接内容可供参考](https://www.cnblogs.com/soldierback/p/11714052.html)