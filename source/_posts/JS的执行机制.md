---
title: JS的执行机制
date: 2020-09-07 21:58:46
categories:
    - JavaScript
tags:
    - 面试题
---
网上很多js机制相关的讲解，本篇只列出相关题目以便以后复习巩固
<!--more-->
{% note %}
js执行机制相关讲解可参考以下博客
https://juejin.im/post/6855129007558492174
https://juejin.im/post/6844903568814800904
{% endnote %}
### 任务队列

任务队列就是一个事件队列,我们前面提到的有些耗时的任务或一些异步任务,我们将它放到任务队列中.当满足某些条件时就可以从任务队列中取出去放到主线程中执行. 我们先来看下面的代码,类似的问题是有可能出现在一些面试题中的.问的就是打印出来的顺序是多少?
```javascript
setTimeout(() => {
  console.log(1)
})
console.log(2)
new Promise((resolve, reject) => {
  console.log(3)
  resolve()
}).then(() => {
  console.log(4)
})
// 2 3 4 1
```
为什么是 2341 的顺序呢?我们来分析下,JS中代码的执行是从上到下一行一行执行的.首先执行的是 setTimeout 这段代码,发现这是定时器任务,于是便把内部的具体执行内容 console.log(1) 先拿出来放到其他地方,准备待会儿再执行.继续执行到 console.log(2) 这句,于是先输出一个2.继续执行,遇到了一个 Promise .注意在这个 Promise中 , console.log(3) 以及之后的 resolve() 这两句都是同步执行的,但是 then 里面的代码却是异步执行的.于是在输出了一个3之后,又把 console.log(4) 拿出来放到其他地方,准备晚点再去执行它.好了,现在我们已经把 console.log(1) 和 console.log(4) 扔进了一个地方,那么为什么是先输出4然后再是1呢?这是因为虽然1和4都被我们扔进了一个地方,我们可以把这个地方理解为一个大房子.1和4被扔进了不同的房间.其中1被扔进了一个叫做 宏任务队列 的房间.4被扔进了另一个叫做 微任务队列 的房间.

**总结起来就是:**不同类型的任务会进入到对应的事件队列(Event Queue)中.每次执行下一个宏任务之前先去微任务队列里面查看,直到把微任务队列清空后再去执行宏任务队列中的任务.

### 微任务和宏任务
- **微任务(micro-task)**: Promise ,process.nextTick 等
- **宏任务(macro-task)**: script, setTimeout, setInterval 等

```javascript
setTimeout(() => {
  console.log(3)
})
process.nextTick(() => {
  console.log(1)
})
console.log(2)
// 2 1 3
```
可以看到输出的结果为 2 1 3 ,这就是因为 setTimeout 是个宏任务, 而 nextTick 则是个微任务.所以先清空微任务队列,即先执行 process.nextTick 的回调.

### 运用微任务和宏任务来解题

到这里我们已经介绍了同步异步以及微任务队列和宏任务队列的相关内容了,我们再来看一看一道复杂一点的题目.
```javascript
setTimeout(() => {
  console.log(1)
})

setTimeout(() => {
  new Promise((resolve, reject) => {
    console.log(2)
    resolve()
  }).then(() => {
    console.log(3)
  })
})

console.log(4)

new Promise((resolve, reject) => {
  console.log(5)
  resolve()
}).then(() => {
  console.log(6)
})

new Promise((resolve, reject) => {
  console.log(7)
  setTimeout(() => {
     console.log(8)
  })
  resolve()
}).then(() => {
  console.log(9)
})
// 4 5 7 6 9 1 2 3 8
```
首先执行前面两个 setTimeout ,于是把123放到了宏任务队列中.执行到4的时候,先打印出一个4.然后是两个Promise,先打印出5,然后把6放到了微任务队列中.再之后打印出7,把8放到宏任务中,然后就是9放到微任务中.此时已经打印出457,并且微任务中有[6,9],宏任务中有[1,2,3,8].代码第一遍已经执行完毕,前面提到了整个script 脚本相当于一个宏任务.于是便去执行微任务,接着打印出69.此时微任务已经清空,去执行宏任务.选取宏任务队列中的第一个任务,打印出1之后.回过头去看看微任务队列是否还有未执行的任务,现在已经没有了.于是便继续执行宏任务队列中的下一个任务即2.打印出2之后,因为这是一个 Promise ,所以将then里面的3放到微任务队列,此次宏任务执行完毕.此时的微任务队列有[3],宏任务队列有[8].再去执行微任务队列,打印出3.最后再次执行宏任务队列,打印出8.