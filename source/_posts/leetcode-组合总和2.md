---
title: leetcode-组合总和2
date: 2020-09-11 22:40:54
categories:
    - 算法与数据结构
tags:
    - dfs
    - 回溯
    - 递归
    - leetcode
---
给定一个数组 candidates 和一个目标数 target ，找出 candidates 中所有可以使数字和为 target 的组合。
candidates 中的每个数字在每个组合中只能使用一次。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/combination-sum-ii/
> 题解转自：https://leetcode-cn.com/problems/combination-sum-ii/solution/man-tan-wo-li-jie-de-hui-su-chang-wen-shou-hua-tu-/

**思路**
以 [2,5,2,1,2], target = 5 为例。

每个节点都作出选择，选一个数，看看下一个选择受到哪些限制：选过的不能再选，且不能产生相同的组合。去做剪枝（如下图所标）。

当和为 target，找到一个正确的解，加入解集。且当和 >= target，已经爆掉了，不用继续选了，结束当前递归，继续搜别的分支，找齐所有的解。

因此，回到上一个节点，撤销当前选择的数字，去进入下一个分支。
**与组合总和区别**
- 组合总和：元素可以重复使用，组合不能重复。
- 本题：元素不可以重复使用，组合不能重复。

本题只需改动三点：
1. 给定的数组可能有重复的元素，先排序，使得重复的数字相邻，方便去重。
2. for 循环枚举出选项时，加入下面判断，忽略掉同一层重复的选项，避免产生重复的组合。比如[1,2,2,2,5]中，选了第一个 2，变成 [1,2]，第一个 2 的下一选项也是 2，跳过它，因为选它，就还是 [1,2]。
```javascript
if (candidates[i - 1] == candidates[i] && i - 1 >= start) {
    continue;
}
```
3. 当前选择的数字不能和下一个选择的数字重复，递归传入i+1，避免与当前选的i重复，这样每次选，就不会选过往选过的同一个数。
```javascript
dfs(i + 1, temp, sum + candidates[i]);
```
**代码**
```javascript
const combinationSum2 = (candidates, target) => {
  candidates.sort();    // 排序
  const res = [];

  const dfs = (start, temp, sum) => { // start是索引 当前选择范围的第一个
    if (sum >= target) {        // 爆掉了，不用继续选了
      if (sum == target) {      // 满足条件，加入解集
        res.push(temp.slice()); // temp是地址引用，后续还要用，所以拷贝一份
      }
      return;                   // 结束当前递归
    }
    for (let i = start; i < candidates.length; i++) {             // 枚举出选择
      if (candidates[i - 1] == candidates[i] && i - 1 >= start) { // 当前选项和隔壁选项一样，跳过
        continue;
      }
      temp.push(candidates[i]);              // 作出选择
      dfs(i + 1, temp, sum + candidates[i]); // 递归，向下选择，并更新sum
      temp.pop();                            // 撤销选择，
    }
  };

  dfs(0, [], 0);
  return res;
};
```