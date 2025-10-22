import java.util.Random;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class AB {



    /* ------------ A ------------ */
    public static class A {
        // knownrebecs
        B b;

        public A(){ self.m1(); }
        // We trigger that via start() so wiring can happen first.

        // msgsrv m1(){ b.m2(); }
        public void m1() {
            b.m2();
        }
    }

    /* ------------ B ------------ */
    public static class B {
        // knownrebecs
        A a;

        // statevars
        int x;

        private final Random rnd = new Random();

        public B() {
            x = 0;
        }

        // msgsrv m2(){ ... }
        public void m2() {
            x = choose(0, 1, 2);

            // Previously called c.m3() with various delays; C is removed, so no-op here.

           
        }

        private int choose(int... options) {
            return options[rnd.nextInt(options.length)];
        }
    }

    /* ------------ main wiring ------------ */
    public static void main(String[] args) throws Exception {
        // Construct
        A a = new A();
        B b = new B();

        // Wire knownrebecs
        a.b = b;
        b.a = a;

        // Start behavior that Rebeca would trigger in A's constructor
        a.start();

        // (Optional) Let it run for a while, then stop.
        // Thread.sleep(10000);
        // EXEC.shutdownNow();
    }
}
