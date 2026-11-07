#include <iostream>

int main(){
    int n, counter, maxnum,curmax;

    std::cout << "please enter number for collatz conjecture:" << std::endl;
    std::cin >> n;
    maxnum = n;
    counter = 1;
    while(n != 1){

        if(n%2 == 0){
            n = n/2;
        }
        else{
            n = (3*n)+1;
            if(n > maxnum){
                maxnum = n;
            }
        }
        counter = counter+1;
        std::cout << n << std::endl;
        }
     std::cout << "this took " << counter << " iterations" << std::endl;
     std::cout << "the maximum number was " << maxnum << std::endl;
        

    }

