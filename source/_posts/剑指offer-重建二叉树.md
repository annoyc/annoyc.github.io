---
title: 剑指offer-重建二叉树
date: 2020-09-21 17:57:55
categories:
    - 算法与数据结构
tags:
    - 二叉树
    - 递归
    - 剑指offer
---
输入某二叉树的前序遍历和中序遍历的结果，请重建该二叉树。假设输入的前序遍历和中序遍历的结果中都不含重复的数字。

<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/zhong-jian-er-cha-shu-lcof/

**代码**
```javascript
var buildTree = function(preorder, inorder) {
    if(!preorder.length || !inorder.length) return null
    let root = preorder[0]
    let node = new TreeNode(root)
    let idx = inorder.indexOf(root)
    node.left = buildTree(preorder.slice(1, idx+1), inorder.slice(0, idx))
    node.right = buildTree(preorder.slice(idx+1), inorder.slice(idx+1))
    return node
};
```