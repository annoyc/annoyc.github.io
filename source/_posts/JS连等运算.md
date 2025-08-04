---
title: JS连等运算
date: 2020-09-06 16:29:39
categories:
    - JavaScript
tags:
    - 面试题
---
js连等运算知识点考查
<!--more-->
- 输出以下代码的结果并解释为什么
```javascript
var a = { n: 1 }
var b = a
a.x = a = { n: 2 } // a = { n: 2 } => { n: 2 }.x = a
console.log(a.x) // undefined
console.log(b.x) // { n: 2 }
```
解释：这里的重点是a.x到底是谁
简单来说，在赋值过程开始时，a其实是{n:1}
a.x=a={n:2}
其实在计算机眼中是长成这样的：
{n:1}.x=a={n:2}
所以，这个赋值发生了两件事
//1.把"a"变成了{n:2}
//2.把{n:1}的x变成了{n:2}--------------------
也就是说：
a.x = a = {n: 2};
其实被计算机执行成了
{n:1}.x={n: 2};
a={n: 2};
所以最后的结果变成了
a=={n: 2};
console.log(a.x) => undefined    //因为a没有x属性
b=={n: 1, x: {n: 2}}
console.log(b.x) => {n: 2}

- 连等开始之前，程序会把所有引用都保存起来
- 连等的过程中，这些值是不变的
- 等到整个连等结束了，再一起变