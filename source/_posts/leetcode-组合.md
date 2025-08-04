---
title: leetcode-组合
date: 2020-09-09 00:43:11
categories:
    - 算法与数据结构
tags:
    - dfs
    - 回溯
    - 递归
    - leetcode
---
给定两个整数 n 和 k，返回 1 ... n 中所有可能的 k 个数的组合。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/combinations/

### 回溯剪枝
直接看代码
```javascript
const combine = (n, k) => {
  const res = [];

  const dfs = (start, path) => { // start是枚举选择的起点 path是当前构建的路径（组合）
    if (path.length == k) {
      res.push(path.slice());       // 拷贝一份path，推入res
      return;                       // 结束当前递归
    }
    for (let i = start; i <= n; i++) { // 枚举出所有选择
      path.push(i);                    // 选择
      dfs(i + 1, path);             // 向下继续选择
      path.pop();                      // 撤销选择
    }
  };

  dfs(1, []); // 递归的入口，从数字1开始选
  return res;
}
```
