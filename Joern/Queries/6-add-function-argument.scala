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

  // HTML-escape for Graphviz HTML-like labels
  def html(s: String): String =
    s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

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

  // ---------------- abstraction with root relabel ----------------

  case class Abstracted(inner: String,
                        lineToKeptNodeId: Map[String,String],  // raw ids (unprefixed)
                        rootIds: Set[String])                    // raw ids (unprefixed)

  def abstractDotByLine(dot: String, rootLabelOverride: Option[String] = None): Abstracted = {
    import scala.util.matching.Regex

    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r
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

    val labelRootIds: Set[String] =
      simpleLabelRx.findAllMatchIn(dot).map(_.group(1)).toSet

    val srcs = edges.map(_._1).toSet
    val dsts = edges.map(_._2).toSet
    val structuralRootIds: Set[String] = srcs -- dsts

    val rootIds: Set[String] = (labelRootIds ++ structuralRootIds)

    if (nodes.isEmpty && edges.isEmpty)
      return Abstracted("""""", Map.empty, Set.empty)

    val bestByLine: Map[String, Node] =
      nodes.groupBy(_.line).map { case (line, xs) => line -> xs.maxBy(_.code.length) }

    val nodeById: Map[String, Node] = nodes.iterator.map(n => n.id -> n).toMap

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

    val rootLines: List[String] =
      if (rootIds.nonEmpty) {
        val text = html(rootLabelOverride.getOrElse("METHOD"))
        rootIds.toList.map { id => s""""$id" [label = <<FONT>${text}</FONT>> ]""" }
      } else Nil

    val inner = (nodeLines ++ edgeLines ++ rootLines).mkString("\n")
    val keptMap: Map[String,String] = bestByLine.map{ case (ln, n) => ln -> n.id }
    Abstracted(inner, keptMap, rootIds)
  }

  // ---------------- main entry ----------------

  def run(fileRegex: String = ".*\\.java"): String = {
    // 1) collect methods in scope
    val methods =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    val methodsByOwner =
      methods.groupBy { m => m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName)) }

    // 2) first pass: abstract each method, keep mappings we need for act-deps
    val methodToPrefixedRootId = scala.collection.mutable.Map.empty[String, String]   // callee-target
    val methodToLineMap        = scala.collection.mutable.Map.empty[String, Map[String,String]] // caller source (line -> node)
    val methodToBody           = scala.collection.mutable.Map.empty[String, String]

    methods.foreach { m =>
      val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
      val pfx   = (owner + "__" + m.fullName).replaceAll("[^A-Za-z0-9_]", "_")
      val raw   = m.dotCfg.l.headOption.getOrElse("")
      if (raw.nonEmpty) {
        val rootLabel = prettyMethodLabel(m)
        val abs = abstractDotByLine(raw, Some(rootLabel)) // no wrapper
        val withWrapper =
          s"""digraph "cfg_abstract" {
node [shape="rect"];
${abs.inner}
}"""
        val prefixed  = prefixIds(withWrapper, pfx)
        val stripped  = stripDotWrapper(prefixed)

        val lineMapPrefixed = abs.lineToKeptNodeId.map{ case (ln,id) => ln -> s"${pfx}_${id}" }
        val rootIdsPrefixed = abs.rootIds.map(id => s"${pfx}_${id}")

        methodToLineMap += (m.fullName -> lineMapPrefixed)
        methodToBody    += (m.fullName -> stripped)
        rootIdsPrefixed.headOption.foreach(r => methodToPrefixedRootId += (m.fullName -> r))
      }
    }

    // 3) build cluster bodies
    val clustersByClass: List[String] =
      methodsByOwner.toList.sortBy(_._1).flatMap { case (owner, ms) =>
        val ownerSan = owner.replaceAll("[^A-Za-z0-9_]", "_")
        val bodies: List[String] =
          ms.flatMap(m => methodToBody.get(m.fullName))
        if (bodies.isEmpty) None
        else Some(
s"""subgraph "cluster_$ownerSan" {
  label="${html(owner)}";
  ${bodies.mkString("\n")}
}"""
        )
      }

    // 4) act-dependency edges: from call-site node (in caller) to callee root node
    val internalFullNames: Set[String] = methodToPrefixedRootId.keySet.toSet

    val actDepEdges: Set[String] = methods.flatMap { callerM =>
      val callerMap = methodToLineMap.getOrElse(callerM.fullName, Map.empty)
      callerM.call.l.flatMap { c =>                 // <-- no typed lambda here
        val callee = Option(c.methodFullName).getOrElse("")
        val lineOpt = c.lineNumber.map(_.toString)   // Option[String]
        (lineOpt, internalFullNames.contains(callee)) match {
          case (Some(ln), true) =>
            val srcIdOpt = callerMap.get(ln)
            val dstIdOpt = methodToPrefixedRootId.get(callee)
            (srcIdOpt, dstIdOpt) match {
              case (Some(src), Some(dst)) if src != dst =>
                Some(s""""$src" -> "$dst" [label="act dep"]""")
              case _ => None
            }
          case _ => None
        }
      }
    }.toSet

    // 5) stitch the whole program-level DOT
    val out =
      s"""digraph "program_cfg_abstract_by_class" {
compound=true;
node [shape="rect"];

${clustersByClass.mkString("\n\n")}

${actDepEdges.mkString("\n")}
}"""

    println(out)
    out
  }
}
val dot = CfgByClass.run()