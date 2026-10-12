/* Minimal xtest harness for the SHARC interpreter: no UART, no timer.
   test_main()'s result lands in xt_result and in R0 on return to startup's .halt. */
extern int test_main(void);
volatile int xt_result;
volatile int xt_done;
int main(void)
{
   xt_result = test_main();
   xt_done = 1;
   return xt_result;
}
