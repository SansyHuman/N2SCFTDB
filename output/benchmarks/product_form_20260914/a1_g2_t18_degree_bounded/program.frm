#: MaxTermSize 600000
On statistics;
S m,n,y,idx1,idx2,j,z,w,u,t(:18);
CF d,C0,C1,C2,C3;
PolyRatFun d;
Function Kvec,Khyp;

L J=sum_(idx1,0,6,m^idx1)
   *sum_(idx2,0,6,n^idx2);
id m=t^3*y;
id n=t^3/y;
.sort

L itotal=sum_(j,1,9,(Kvec(t^j,y^j,u^j)*C0(j)+Kvec(t^j,y^j,u^j)*C1(j)+Khyp(t^j,y^j,u^j)*C2(j)+Khyp(t^j,y^j,u^j)*C2(j)*C3(j)+6*Khyp(t^j,y^j,u^j)*C3(j))/j);
id Kvec(t?,y?,u?)=J*(t^2*u^2-t^4/u^2-t^3*y-t^3/y+2*t^6);
id Khyp(t?,y?,u?)=J*(t^2/u-t^4*u);
.sort


Bracket t;
.sort
Skip J,itotal;
L I=z*itotal;
.sort
#do i=2,9
  Skip J,itotal;
  #do k=2,16
    if (count(t,1) == `k');
      id z=1+w*sum_(idx1,2,18-`k',itotal[t^idx1]*t^idx1)/`i';
    endif;
  #enddo
  id z=1;
  id w=z;
  .sort:step `i';
#enddo
L result=1+I;
id z=1;
.sort
Print result;
.end
