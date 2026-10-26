# SPDX-License-Identifier: GPL-2.0-or-later
import math,sys
th=float(sys.argv[1]); flip=len(sys.argv)>2
a=1/3+2/3*math.cos(th); b=1/3+2/3*math.cos(th-2*math.pi/3); c=1/3+2/3*math.cos(th+2*math.pi/3)
M=[[a,b,c],[c,a,b],[b,c,a]]
if flip: M=[[M[i][j]-2/3 for j in range(3)] for i in range(3)]  # H*R: Householder on the sum mode
  # (I - 2/3 J) R = R - 2/3 J R; J R = J since rows of R sum to 1
print("static const float SP_MIX[9] = { "+", ".join("%.8ff"%v for r in M for v in r)+" };")
