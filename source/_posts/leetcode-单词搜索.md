---
title: leetcode-单词搜索
date: 2020-09-16 20:30:03
categories:
    - 算法与数据结构
tags:
    - 回溯
    - dfs
    - 递归
    - leetcode
---
给定一个二维网格和一个单词，找出该单词是否存在于网格中。

单词必须按照字母顺序，通过相邻的单元格内的字母构成，其中“相邻”单元格是那些水平相邻或垂直相邻的单元格。同一个单元格内的字母不允许被重复使用。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/word-search/
> 题解转自：https://leetcode-cn.com/problems/word-search/solution/shou-hua-tu-jie-79-dan-ci-sou-suo-dfs-si-lu-de-cha/

**题意**
给你一个由字母组成的二维矩阵，和一个单词，能否在矩阵中“勾勒”出一条路径，路径上的字母组成了这个单词。

**思路**
比如单词 "SEE"，首先起点要对，遍历一遍矩阵，看看哪里有起点 S。
起点可能不止一个，基于其中一个 S，能否找出剩下的 "EE" 路径。其实已经有了深搜的思路了。
下一个字符 E，有四个可选点：当前 S 点的上、下、左、右。
逐个尝试每一种选择，去探索。基于其中一种选择，又要为下一个字符选点，又有四种选择，继续尝试，探索。回溯的思路就有了。
每到一个点，做的事情是一样的，是递归DFS。路径类问题经常是 DFS——往下选点，构建路径。
当发现某个选择不对，不要继续选下去了，结束当前递归，考察别的选择。

**递归总是关注当前**
我们在写递归函数时，关注当前，当前考察的点，哪些是当前递归该处理的，哪些是丢给递归子调用去做的。
当前递归本身做的事：判断当前选择的点，本身有没有问题，是不是错的。
至于剩下的字符，能否找到路径，这件事，交给递归子调用去深搜。
如果当前点是错的，也不用往下递归了，直接返回false。否则继续递归搜四个方向，为剩下的字符选点。

**哪些情况说明这是一个错的选择呢？（递归的结束条件）**
1. 当前的点，超出矩阵，不存在。
2. 当前的点，之前来过了，不符合「同一个单元格内的字母不允许被重复使用」
3. 当前的点，不是目标点，比如你想找 E，却来了 D。

**当前的点，不是目标点，比如你想找 E，却来了 D。**
用一个二维矩阵 used，记录已经访问过的点，下次再选择访问这个点，就直接返回 false。

**代码**
```javascript
const exist = (board, word) => {
  const m = board.length;
  const n = board[0].length;
  const used = new Array(m);    // 二维矩阵used
  for (let i = 0; i < m; i++) {
    used[i] = new Array(n);
  }
  // 判断当前点是否是目标路径上的点
  const dfs = (row, col, i) => { // row col是当前点的坐标，i是当前考察的字符索引
    if (i > word.length - 1) {       // 递归的出口
      return true;
    }
    if (row < 0 || row >= m || col < 0 || col >= n) { // 当前点要存在
      return false;
    }
    if (used[row][col] || board[row][col] != word[i]) { // 当前的点已经走过，或当前点就不是目标点
      return false;
    }
    // 排除掉这些false情况，当前点是没问题的，可以继续递归考察
    used[row][col] = true;  // used记录一下当前点被访问了
    const canFindRest =
      dfs(row + 1, col, i + 1) ||
      dfs(row - 1, col, i + 1) ||
      dfs(row, col + 1, i + 1) ||
      dfs(row, col - 1, i + 1); 

    if (canFindRest) { // 基于当前点，可以为剩下的字符找到路径
      return true;    
    }
    used[row][col] = false; // 找不出，返回false，继续考察别的分支，并撤销当前点的访问状态。
    return false;
  };

  for (let i = 0; i < m; i++) {
    for (let j = 0; j < n; j++) {
      if (board[i][j] == word[0] && dfs(i, j, 0)) { // 找到dfs的起点
        return true; // 找到起点，且dfs的结果也true，则找到了目标路径
      }
    }
  }
  return false; // 怎么样都没有返回true，则返回false
};
```