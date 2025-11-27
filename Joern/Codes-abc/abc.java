import java.util.Random;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class ABC {



    /* ------------ A ------------ */
    public static class A {
        // knownrebecs
        B b;

        // In Rebeca: A(){ self.m1(); }
        // We trigger that via start() so wiring can happen first.
  
        public A(){ self.m1(); }

        // msgsrv m1(){ b.m2(); }
        public void m1() {
            b.m2();
        }
    }

    /* ------------ B ------------ */
    public static class B {
        // knownrebecs
        A a;
        C c;

        // statevars
        int x;

        private final Random rnd = new Random();

        public B() {
            x = 0;
        }

        // msgsrv m2(){ ... }
        public void m2() {
            x = choose(0, 1, 2);

            if (x == 0) {
             c.m3();
            } else if (x == 1) {
             c.m3();
            } else {
                c.m3();
            }

            a.m1()
        }

        private int choose(int... options) {
            return options[rnd.nextInt(options.length)];
        }
    }

    /* ------------ C ------------ */
    public static class C {
        // statevars
        boolean x;

        private final Random rnd = new Random();

        public C() {
            x = false;
        }

        // msgsrv m3(){ x = ?(true, false); }
        public void m3() {
            x = rnd.nextBoolean();
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
        b.a = a;
        b.c = c;


        // (Optional) Let it run for a while, then stop.
        // Thread.sleep(10000);
        // EXEC.shutdownNow();
    }
}
