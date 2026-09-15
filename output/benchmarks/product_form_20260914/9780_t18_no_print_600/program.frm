#: MaxTermSize 600000
On statistics;
S m,n,y,idx1,idx2,j,z,u,t(:18);
CF d,C0,C1,C2,C3,C4,C5,C6,C7,C8,C9;
PolyRatFun d;
Function Kvec,Khyp;

L J=sum_(idx1,0,6,m^idx1)
   *sum_(idx2,0,6,n^idx2);
id m=t^3*y;
id n=t^3/y;
.sort

L itotal=sum_(j,1,9,(Kvec(t^j,y^j,u^j)*C0(j)+Kvec(t^j,y^j,u^j)*C1(j)+Khyp(t^j,y^j,u^j)*C2(j)+Khyp(t^j,y^j,u^j)*C3(j)+Khyp(t^j,y^j,u^j)*C4(j)+Khyp(t^j,y^j,u^j)*C5(j)+Khyp(t^j,y^j,u^j)*C6(j)+Khyp(t^j,y^j,u^j)*C7(j)+Khyp(t^j,y^j,u^j)*C8(j)+Khyp(t^j,y^j,u^j)*C9(j))/j);
id Kvec(t?,y?,u?)=J*(t^2*u^2-t^4/u^2-t^3*y-t^3/y+2*t^6);
id Khyp(t?,y?,u?)=J*(t^2/u-t^4*u);
.sort

L I=z;
id z=z*itotal;
#do i=2,9
  id z=1+z*itotal/`i';
  .sort:step `i';
#enddo
.sort

L result=1+I;
id z=1;
.sort

.end
