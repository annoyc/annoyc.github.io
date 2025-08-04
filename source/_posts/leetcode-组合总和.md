---
title: leetcode-组合总和
date: 2020-09-09 14:44:49
categories:
    - 算法与数据结构
tags:
    - dfs
    - 回溯
    - 递归
    - leetcode
---
给定一个无重复元素的数组 candidates 和一个目标数 target ，找出 candidates 中所有可以使数字和为 target 的组合。
candidates 中的数字可以无限制重复被选取。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/combinations/
> 题解转自：https://leetcode-cn.com/problems/combination-sum/solution/shou-hua-tu-jie-zu-he-zong-he-combination-sum-by-x/

**题意**
给你一个数组，里面都是不带重复的正数，还给你一个 target，求出所有和为 target 的组合。
元素可以重复使用，但组合不能重复，比如 [2, 2, 3] 与 [2, 3, 2] 是重复的。
**参考图解**
![avatar](https://cdn.jsdelivr.net/gh/yc2hang/cdn-assets/photos/combinationsum.png)
**代码**
```javascript
const combinationSum = (candidates, target) => {
  const res = [];
  let sum = 0;

  const dfs = (start, temp) => {
    if (sum >= target) {
      if (sum == target) {
        res.push(temp.slice());
      }
      return;  // 结束当前递归
    }
    for (let i = start; i < candidates.length; i++) { // 枚举出选择，从start开始
      sum += candidates[i];     // 累加给sum
      temp.push(candidates[i]); // 加入“部分解”
      dfs(i, temp);             // 往下递归，继续选择，注意是i，不是i+1
      sum -= candidates[i];     // 撤销选择，sum变回来
      temp.pop();               // 撤销选择
    }
  };

  dfs(0, []);
  return res;
};
```
**总结**
{% note %}
回溯：在包含问题的所有的解的空间树中，按DFS的方法，从根节点出发，搜索整棵解空间树。

搜索至任何一个节点时，总是会先判断当前节点是否可以 lead us to a complete solution。如果不可以，则结束对「以当前节点为根节点的子树」的搜索，向父节点回溯，回到之前的状态，搜索下一个分支。

否则，进入该子树，继续以DFS的方式搜索。

空间树中的节点是动态的，当前有哪些节点可选择，是根据上一步的选择（上一步的状态）得出的，所以做回溯时，要把状态还原成进入当前节点之前的状态。

我们在做题时，要确定出问题的解空间树，它是隐式的，不是显式的一棵树。不熟练的就画图看看。

然后，明确每个节点的扩展搜索规则。

然后进行DFS搜索，并注意剪枝，避免无效的搜索。
{% endnote %}


