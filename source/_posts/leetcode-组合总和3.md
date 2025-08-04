---
title: leetcode-组合总和3
date: 2020-09-12 00:33:43
categories:
    - 算法与数据结构
tags:
    - dfs
    - 回溯
    - 递归
    - leetcode
---
找出所有相加之和为 n 的 k 个数的组合。组合中只允许含有 1 - 9 的正整数，并且每种组合中不存在重复的数字。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/combination-sum-iii/
> 题解转自：https://leetcode-cn.com/problems/combination-sum-iii/solution/shou-hua-tu-jie-216-zu-he-zong-he-iii-by-xiao_ben_/

**题意**
此题与组合总和1，2相似，解法可参考前面
**代码**
```javascript
const combinationSum3 = (k, n) => {
  const res = []; 

  const dfs = (start, temp, sum) => {
    if (temp.length == k) {     // You've selected k numbers. End recursion
      if (sum == n) {           // The sum of numbers in a combination equals n
        res.push(temp.slice()); // Add its copy to the solution set
      }
      return;
    }
    for (let i = start; i <= 9; i++) { // Enumerate the options
      temp.push(i);                    // Make a choice
      dfs(i + 1, temp, sum + i);       // Explore
      temp.pop();                      // Undo the choice
    }
  };

  dfs(1, [], 0);  // press the search button
  return res;
};
```

