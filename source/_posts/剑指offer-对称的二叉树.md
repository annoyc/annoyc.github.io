---
title: 剑指offer-对称的二叉树
date: 2020-09-21 18:21:49
categories:
    - 算法与数据结构
tags:
    - 递归
    - 剑指offer
---
请实现一个函数，用来判断一棵二叉树是不是对称的。如果一棵二叉树和它的镜像一样，那么它是对称的。

<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/dui-cheng-de-er-cha-shu-lcof/

**思路**
对于树中 任意两个对称节点L和R 一定有：
    - L.val=R.val ：即此两对称节点值相等。
    - L.left.val=R.right.val 
    - L.right.val=R.left.val

**代码**
```javascript
/**
 * Definition for a binary tree node.
 * function TreeNode(val) {
 *     this.val = val;
 *     this.left = this.right = null;
 * }
 */
/**
 * @param {TreeNode} root
 * @return {boolean}
 */
var isSymmetric = function(root) {
    var helper = function(left_root, right_root){
        if(!left_root && !right_root) return true
        if(!left_root || !right_root) return false
        if(left_root.val !== right_root.val) return false
        return helper(left_root.left, right_root.right) && helper(left_root.right, right_root.left)
    }
    return !root ? true : helper(root.left, root.right)
};
```