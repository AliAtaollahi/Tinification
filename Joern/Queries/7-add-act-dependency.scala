import io.shiftleft.semanticcpg.language._
import io.shiftleft.codepropertygraph.generated.nodes.Method

/**
 * Usage in Joern:
 * :load /path/to/this.scala
 * val dot = CfgByClass.run(".*HVAC\\.java")  // or omit arg to include all .java
 */
object CfgByClass {

  // ---------------- helpers ----------------

  def ownerTypeOf(fullName: String): String =
    fullName.takeWhile(_ != '.')

  def simpleTypeName(tf: String): String =
    Option(tf).getOrElse("").split("[.$]").lastOption.getOrElse(tf)

  // Build "Owner.method(p1, p2, ...)" from CPG; prefer param names, fall back to types; skip implicit 'this'
  def prettyMethodLabel(m: Method): String = {
    val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
    val params =
      m.parameter.orderGt(0).l.map { p =>
        val nm  = Option(p.name).map(_.trim).filter(n => n.nonEmpty && n != "this")
        val tpe = simpleTypeName(Option(p.typeFullName).getOrElse(""))
        nm.getOrElse(tpe)
      }.mkString(", ")
    s"$owner.${m.name}($params)"
  }

  // Prefix every numeric node id with a stable method-based prefix
  def prefixIds(dot: String, prefix: String): String = {
    val idRx = "\"(\\d+)\"".r
    idRx.replaceAllIn(dot, m => "\"" + prefix + "_" + m.group(1) + "\"")
  }

  // Remove outer digraph wrapper (so we can drop inside clusters)
  def stripDotWrapper(dot: String): String =
    dot.linesIterator
      .filterNot(_.trim.startsWith("digraph"))
      .filterNot(_.trim.startsWith("node "))
      .filterNot(_.trim == "}")
      .mkString("\n")

  // HTML-escape for Graphviz HTML-like labels
  def html(s: String): String =
    s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

  // ---------------- abstraction with root relabel ----------------

  /**
   * Collapses nodes per source line to the longest code, rewires edges accordingly,
   * and (re)labels the method root node(s) with `rootLabelOverride` if provided.
   * IMPORTANT: root label lines are emitted LAST to override earlier defs.
   */
  def abstractDotByLine(dot: String, rootLabelOverride: Option[String] = None): String = {
    import scala.util.matching.Regex

    // "normal" nodes: "42" [label=<KIND, 123<BR/>code...>]
    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r

    // simple label nodes: "7" [label=<getSense>]
    val simpleLabelRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,><]+)>\\s*\\]".r

    val edgeRx: Regex =
      "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

    case class Node(id: String, kind: String, line: String, code: String, methodName: Option[String] = None)

    val nodes: List[Node] =
      nodeRx.findAllMatchIn(dot).map { m =>
        val kind = m.group(2)
        val code = m.group(4)
        val methName =
          if (kind.startsWith("METHOD") || kind.equalsIgnoreCase("init")) Some(code) else None
        Node(m.group(1), kind, m.group(3), code, methName)
      }.toList

    val edges: List[(String, String)] =
      edgeRx.findAllMatchIn(dot).map(m => (m.group(1), m.group(2))).toList

    // Root candidates by label form
    val labelRootIds: Set[String] =
      simpleLabelRx.findAllMatchIn(dot).map(_.group(1)).toSet

    // Structural roots: nodes that appear as a source but never as a destination
    val srcs = edges.map(_._1).toSet
    val dsts = edges.map(_._2).toSet
    val structuralRootIds: Set[String] = srcs -- dsts

    val rootIds: Set[String] = (labelRootIds ++ structuralRootIds)

    if (nodes.isEmpty && edges.isEmpty)
      return """digraph "cfg_abstract" { node [shape="rect"]; }"""

    val bestByLine: Map[String, Node] =
      nodes.groupBy(_.line).map { case (line, xs) => line -> xs.maxBy(_.code.length) }

    val nodeById: Map[String, Node] = nodes.iterator.map(n => n.id -> n).toMap

    // Kept nodes (one per line)
    val keptNodes = bestByLine.values.toList
    val nodeLines: List[String] =
      keptNodes
        .sortBy(n => n.line.toIntOption.getOrElse(Int.MaxValue))
        .map { n =>
          val label =
            n.kind match {
              case k if k.startsWith("METHOD") && n.methodName.isDefined => n.methodName.get
              case k if k.equalsIgnoreCase("init") && n.methodName.isDefined => n.methodName.get
              case _ => s"${n.kind}, ${n.line}<BR/>${n.code}"
            }
          s""""${n.id}" [label = <${label}> ]"""
        }

    def isRoot(id: String): Boolean = rootIds.contains(id)

    val edgeLines: List[String] =
      edges.flatMap { case (srcId, dstId) =>
        val adjSrc = nodeById.get(srcId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(srcId)
        val adjDst = nodeById.get(dstId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(dstId)
        if (adjSrc != adjDst && !isRoot(adjDst))
          Some(s""""$adjSrc" -> "$adjDst" [label="ctrl dep"]""")
        else None
      }.toSet.toList

    // Emit root label overrides LAST and wrapped in a valid HTML element (<FONT>)
    val rootLines: List[String] =
      if (rootIds.nonEmpty) {
        val text = html(rootLabelOverride.getOrElse("METHOD"))
        rootIds.toList.map { id => s""""$id" [label = <<FONT>${text}</FONT>> ]""" }
      } else Nil

    s"""digraph "cfg_abstract" {
node [shape="rect"];
${(nodeLines ++ edgeLines ++ rootLines).mkString("\n")}
}"""
  }

  // ---------------- main entry ----------------

  def run(fileRegex: String = ".*\\.java"): String = {
    val methods =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    // Group by owner type; prefer typeDecl.name, fallback to fullName prefix
    val methodsByOwner =
      methods.groupBy { m => m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName)) }

    val clustersByClass: List[String] =
      methodsByOwner.toList.sortBy(_._1).flatMap { case (owner, ms) =>
        val ownerSan = owner.replaceAll("[^A-Za-z0-9_]", "_")
        val bodies: List[String] = ms.flatMap { m =>
          val raw = m.dotCfg.l.headOption.getOrElse("")
          if (raw.isEmpty) None
          else {
            val rootLabel = prettyMethodLabel(m)             // Owner.method(params)
            val abs  = abstractDotByLine(raw, Some(rootLabel))
            val pfx  = (owner + "__" + m.fullName).replaceAll("[^A-Za-z0-9_]", "_")
            val body = stripDotWrapper(prefixIds(abs, pfx))
            Some(body)
          }
        }
        if (bodies.isEmpty) None
        else Some(
s"""subgraph "cluster_$ownerSan" {
  label="${html(owner)}";
  ${bodies.mkString("\n")}
}"""
        )
      }

    val out =
      s"""digraph "program_cfg_abstract_by_class" {
compound=true;
node [shape="rect"];
${clustersByClass.mkString("\n\n")}
}"""
    println(out)
    out
  }
}

// limit to HVAC.java (as in your example)
val dot = CfgByClass.run(".*HVAC\\.java")

// or run for all .java files
// val dot = CfgByClass.run()
