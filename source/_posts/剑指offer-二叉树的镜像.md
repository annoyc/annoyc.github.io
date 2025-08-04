---
title: 剑指offer-二叉树的镜像
date: 2020-09-21 18:17:23
categories:
    - 算法与数据结构
tags:
    - 递归
    - bfs
    - 剑指offer
---
请完成一个函数，输入一个二叉树，该函数输出它的镜像。

<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/er-cha-shu-de-jing-xiang-lcof/

### 递归
根据二叉树镜像的定义，考虑递归遍历（dfs）二叉树，交换每个节点的左 / 右子节点，即可生成二叉树的镜像。
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
 * @return {TreeNode}
 */
var mirrorTree = function(root) {
    if(!root || root.length == 0) return null
    let tmp = root.left
    root.left = mirrorTree(root.right)
    root.right = mirrorTree(tmp)
    return root
};
```

### 辅助栈
利用栈（或队列）遍历树的所有节点 node，并交换每个 node 的左 / 右子节点。
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
 * @return {TreeNode}
 */
var mirrorTree = function(root) {
    if(!root || root.length == 0) return null
    let stack = [root]
    while(stack.length){
        let node = stack.pop()
        if(node.left){
            stack.push(node.left)
        }
        if(node.right){
            stack.push(node.right)
        }
        let tmp = node.left
        node.left = node.right
        node.right = tmp
    }
    return root
};
```