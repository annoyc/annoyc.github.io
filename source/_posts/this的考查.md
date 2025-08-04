---
title: this的考查
date: 2020-09-04 20:00:27
categories:
    - JavaScript
tags:
    - 面试题
---
关于this的考查部分题目如下：
<!--more-->
1. 输出代码结果
```javascript
var fullName = 'language'
var obj = {
    fullName = 'javascript',
    prop = {
        getFullName = function(){
            return this.fullName
        }
    }
}
console.log(obj.prop.getFullName()) // this为obj.prop，故输出undefined
var test = obj.prop.getFullName
console.log(test()) // this为window，故输出'language'
```
2. 输出代码结果
```javascript
var name = 'window'
var Tom = {
    name: 'Tom',
    show: function(){
        console.log(this.name)
    },
    wait: function(){
        var fun = this.show
        fun()
    }
}
Tom.wait() // this: Tom => fun = Tom.show =>fun() => this: window => 输出window.name => 'window'
```
3. 输出代码结果
```javascript
function fun(){
    this.a = 0
    this.b = function(){
        alert(this.a)
    }
}
fun.prototype = { // 此时fun的constructor改变，指向了Object
    b: function(){
        this.a = 20
        alert(this.a)
    },
    c: function(){
        this.a = 30
        alert(this.a)
    }
}
var my_fun = new fun()
my_fun.b() // 私有的方法b  this: my_fun => my_fun.a => '0'
my_fun.c() // 公有的方法c  this: my_fun => my_fun.c => '30'(把当前示例私有属性由0改为了30)
```
