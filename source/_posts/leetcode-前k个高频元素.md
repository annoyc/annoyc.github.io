---
title: leetcode-前k个高频元素
date: 2020-09-08 22:10:18
categories:
    - 算法与数据结构
tags:
    - Map
    - 小顶堆
    - 桶排序
    - leetcode
---
给定一个非空的整数数组，返回其中出现频率前 k 高的元素。
<!--more-->
> 原题描述访问：https://leetcode-cn.com/problems/top-k-frequent-elements/

### map+数组
利用 map 记录每个元素出现的频率，利用数组来比较排序元素
**代码实现**
```javascript
let topKFrequent = function(nums, k) {
    let map = new Map(), arr = [...new Set(nums)]
    nums.map((num) => {
        if(map.has(num)) map.set(num, map.get(num)+1)
        else map.set(num, 1)
    })
    
    return arr.sort((a, b) => map.get(b) - map.get(a)).slice(0, k);
};
```
**复杂度分析**
- 时间复杂度：O(nlogn)
- 空间复杂度：O(n)
题目要求算法的时间复杂度必须优于 O(n log n) ，所以这种实现不合题目要求

### map+小顶堆
遍历一遍数组统计每个元素的频率，并将元素值（ key ）与出现的频率（ value ）保存到 map 中
通过 map 数据构建一个前 k 个高频元素小顶堆，小顶堆上的任意节点值都必须小于等于其左右子节点值，即堆顶是最小值。
具体步骤如下：
    - 遍历数据，统计每个元素的频率，并将元素值（ key ）与出现的频率（ value ）保存到 map 中
    - 遍历 map ，将前 k 个数，构造一个小顶堆
    - 从 k 位开始，继续遍历 map ，每一个数据出现频率都和小顶堆的堆顶元素出现频率进行比较，如果小于堆顶元素，则不做任何处理，继续遍历下一元素；如果大于堆顶元素，则将这个元素替换掉堆顶元素，然后再堆化成一个小顶堆。
    - 遍历完成后，堆中的数据就是前 k 大的数据
**代码实现**
```javascript
let topKFrequent = function(nums, k) {
    let map = new Map(), heap = [,]
    nums.map((num) => {
        if(map.has(num)) map.set(num, map.get(num)+1)
        else map.set(num, 1)
    })
    
    // 如果元素数量小于等于 k
    if(map.size <= k) {
        return [...map.keys()]
    }
    
    // 如果元素数量大于 k，遍历map，构建小顶堆
    let i = 0
    map.forEach((value, key) => {
        if(i < k) {
            // 取前k个建堆, 插入堆
            heap.push(key)
            // 原地建立前 k 堆
            if(i === k-1) buildHeap(heap, map, k)
        } else if(map.get(heap[1]) < value) {
            // 替换并堆化
            heap[1] = key
            // 自上而下式堆化第一个元素
            heapify(heap, map, k, 1)
        }
        i++
    })
    // 删除heap中第一个元素
    heap.shift()
    return heap
};

// 原地建堆，从后往前，自上而下式建小顶堆
let buildHeap = (heap, map, k) => {
    if(k === 1) return
    // 从最后一个非叶子节点开始，自上而下式堆化
    for(let i = Math.floor(k/2); i>=1 ; i--) {
        heapify(heap, map, k, i)
    }
}

// 堆化
let heapify = (heap, map, k, i) => {
    // 自上而下式堆化
    while(true) {
        let minIndex = i
        if(2*i <= k && map.get(heap[2*i]) < map.get(heap[i])) {
            minIndex = 2*i
        }
        if(2*i+1 <= k && map.get(heap[2*i+1]) < map.get(heap[minIndex])) {
            minIndex = 2*i+1
        }
        if(minIndex !== i) {
            swap(heap, i, minIndex)
            i = minIndex
        } else {
            break
        }
    }
}

// 交换
let swap = (arr, i , j) => {
    let temp = arr[i]
    arr[i] = arr[j]
    arr[j] = temp
}
```
**复杂度分析：**
- 时间复杂度：遍历数组需要 O(n) 的时间复杂度，一次堆化需要 O(logk) 时间复杂度，所以利用堆求 Top k 问题的时间复杂度为 O(nlogk)
- 空间复杂度：O(n)

### 桶排序
这里取前k个高频元素，使用计数排序不再适合，在上题目中使用计数排序，将 i 元素出现的次数存储在 bucket[i] ，但这种存储不能保证 bucket 数组上值是有序的，例如 bucket=[0,3,1,2] ，即元素 0 未出现，元素 1 出现 3 次，元素 2 出现 1 次，元素 3 出现 2 次，所以计数排序不适用于取前k个高频元素，不过，不用怕，计数排序不行，还有桶排序。

桶排序是计数排序的升级版。它也是利用函数的映射关系。

桶排序 (Bucket sort)的工作的原理：假设输入数据服从均匀分布，将数据分到有限数量的桶里，每个桶再分别排序（有可能再使用别的排序算法或是以递归方式继续使用桶排序进行排）。
- 首先使用 map 来存储频率
- 然后创建一个数组（有数量的桶），将频率作为数组下标，对于出现频率不同的数字集合，存入对应的数组下标（桶内）即可。

**代码实现**
```javascript
let topKFrequent = function(nums, k) {
    let map = new Map(), arr = [...new Set(nums)]
    nums.map((num) => {
        if(map.has(num)) map.set(num, map.get(num)+1)
        else map.set(num, 1)
    })
    
    // 如果元素数量小于等于 k
    if(map.size <= k) {
        return [...map.keys()]
    }
    
    return bucketSort(map, k)
};

// 桶排序
let bucketSort = (map, k) => {
    let arr = [], res = []
    map.forEach((value, key) => {
        // 利用映射关系（出现频率作为下标）将数据分配到各个桶中
        if(!arr[value]) {
            arr[value] = [key]
        } else {
            arr[value].push(key)
        }
    })
    // 倒序遍历获取出现频率最大的前k个数
    for(let i = arr.length - 1;i >= 0 && res.length < k;i--){
        if(arr[i]) {
            res.push(...arr[i])
        }
	}
	return res
}
```
**复杂度分析：**
- 时间复杂度：O(n)
- 空间复杂度：O(n)