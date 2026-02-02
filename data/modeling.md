# Data Modeling & Structure

## Overview

This file contains example tables for the main data models utilized in the SafeAscent SQL db.

### Accidents

| AccidentID | ClimberID | type | date | mountainID | injury | notes |
| :--------: | :-------: | :---:| :--: | :--------: | :----: | :---: |
| ac3298234  | cl291839  | rap  | 9/6  | mt9485333  | true   | leg   |

### Mountains/Crags

| MountainID | name | lon | lon | base elevation | height |
| :--------: | :--: | :-: | :-: | :------------: | :----: |
| mt1000000  | dome | 3.1 | 2.5 | 2500 ft        | 10000  |

### Climbers

| ClimberID | name | dob    | nationality | ticks                       |
| :-------: | :--: | :-:    | :---------: | :-------------------------: |
| cl1000000 | john | 5/6/04 | US.         | burden of dreams, predator  |

### Route

| RouteID | name | mountainID | grade | length | base | lat | lon |
| :-----: | :--: | :--------: | :---: | :----: | :--: | :-: | :-: |
| r100000 | fun1 | mt09342092 | 10A.  | 100 m  | 1000 | 3.1 | 2.5 |

### Ascent

| AscentID | routeid | climberid | date | start | end | notes | acc |
| :------: | :-----: | :-------: | :--: | :---: | :-: | :---: | :-: |
| a1000000 | r238949 | cl4809292 | 5/17 | 10:00 | 1:00|  n/a  | n/a |

### Weather Report

| weatherid | loc | date | time | temp | wind | precip | uv  | vis |
| :-------: | :-: | :--: | :--: | :--: | :--: | :----: | :-: | :-: |
| 000000001 |  ?  | 2/2/2| 10:00| 45   | 23   | "3     | 2   | 12  |
