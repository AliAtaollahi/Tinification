import java.util.Random;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class ABC {



    /* ------------ A ------------ */
    public static class A {
        // knownrebecs
        B b;
         int temp;

        // In Rebeca: A(){ self.m1(); }
        // We trigger that via start() so wiring can happen first.
  
        public A(){ self.m1(); }

        // msgsrv m1(){ b.m2(); }
        public void m1() {
           temp=10;
            b.m2(temp);
        }
    }

    /* ------------ B ------------ */
    public static class B {
        // knownrebecs
 
        C c;
        int t1=0;
 

 

        public B() {
      
        }

        // msgsrv m2(){ ... }
        public void m2(int temp) {
            
           t1=temp+10;
            c.m3(t1)
        }


    }

    /* ------------ C ------------ */
    public static class C {
        // statevars
 

        public C() {
         
        }

        // msgsrv m3(){ x = ?(true, false); }
        public void m3(int temp) {
         
        }
    }

    /* ------------ main wiring ------------ */
    public static void main(String[] args) throws Exception {
        // Construct
        A a = new A();
        B b = new B();
        C c = new C();

        // Wire knownrebecs
        a.b = b;
    
        b.c = c;


        // (Optional) Let it run for a while, then stop.
        // Thread.sleep(10000);
        // EXEC.shutdownNow();
    }
}
