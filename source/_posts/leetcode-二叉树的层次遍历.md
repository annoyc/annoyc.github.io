---
title: leetcode-二叉树的层次遍历
date: 2020-09-06 22:42:33
categories:
    - 算法与数据结构
tags:
    - 二叉树
    - 队列
    - 递归
    - leetcode
---
给你一个二叉树，请你返回其按 层序遍历 得到的节点值。 （即逐层地，从左到右访问所有节点）。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/binary-tree-level-order-traversal/

### bfs
首先定义一个存储结果的数组，将二叉树的每一层分级lv，遍历每个跟节点，将节点的值存入数组，遍历一层lv+1，递归左右子节点将值存入同级数组中，代码如下：
```javascript
var levelOrder = function(root) {
    if(!root || root.length === 0) return []
    let res = []
    let bfs = (curr, lv) => {
        !res[lv] && (res[lv] = [])
        if(curr){
            res[lv].push(curr.val)
            if(curr.left) bfs(curr.left, lv+1)
            if(curr.right) bfs(curr.right, lv+1)
        }
    }
    bfs(root, 0)
    return res
};
```
### 队列
将初始二叉树存入quene中，通过双层while循环存入每层级的值
- 外层while循环的是二叉树每层的数量
- 内层while目的是将每层的每项存入各层数组中

代码如下：
```javascript
var levelOrder = function(root) {
    if(!root || root.length === 0) return []
    var result = []
    var quene = [root]
    while(quene.length){
        var len = quene.length
        var layer = []
        while(len){
            var node = quene.shift()
            layer.push(node.val)
            if(node.left){
                quene.push(node.left)
            }
            if(node.right){
                quene.push(node.right)
            }
            len--
        }
        result.push(layer)
    }
    return result
};
```
